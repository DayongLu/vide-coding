"""Tests for src/invoice_scanner.py — business logic for invoice queue."""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import invoice_scanner
from api.db import init_db


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Create a temp SQLite database and point invoice_scanner at it."""
    db_file = tmp_path / "test_invoices.db"
    init_db(db_file)
    monkeypatch.setattr(invoice_scanner, "_DB_PATH", db_file)
    return db_file


# ---------------------------------------------------------------------------
# get_invoice_queue
# ---------------------------------------------------------------------------

def test_get_invoice_queue_empty(tmp_db):
    """Queue is empty initially."""
    result = invoice_scanner.get_invoice_queue()
    assert result == []


def test_get_invoice_queue_filters_by_status(tmp_db):
    """get_invoice_queue only returns rows matching the requested status."""
    conn = invoice_scanner._get_conn()
    import datetime, uuid
    for status in ("pending", "created", "rejected"):
        conn.execute(
            """INSERT INTO email_invoices
               (id, email_id, subject, from_address, received_at, attachment_name,
                status, extracted_data, vendor_id, vendor_name, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), f"email_{status}", f"Subj {status}", "from@example.com",
             "Mon, 1 Jan 2026", "", status, "{}", None, "Vendor", datetime.datetime.now(datetime.timezone.utc).isoformat()),
        )
    conn.commit()
    conn.close()

    pending = invoice_scanner.get_invoice_queue(status="pending")
    assert len(pending) == 1
    assert pending[0]["status"] == "pending"


# ---------------------------------------------------------------------------
# approve_invoice
# ---------------------------------------------------------------------------

def _insert_pending_invoice(conn, vendor_id="10", vendor_name="Acme Corp"):
    """Helper: insert a pending invoice row and return its ID."""
    import uuid, datetime
    inv_id = str(uuid.uuid4())
    extracted = {
        "vendor_name": vendor_name,
        "invoice_number": "INV-TEST",
        "invoice_date": "2026-04-01",
        "due_date": "2026-04-30",
        "line_items": [{"description": "Consulting", "amount": 500.0}],
        "total_amount": 500.0,
        "currency": "USD",
        "raw_text": "",
    }
    conn.execute(
        """INSERT INTO email_invoices
           (id, email_id, subject, from_address, received_at, attachment_name,
            status, extracted_data, vendor_id, vendor_name, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
        (inv_id, "email123", "Invoice from Acme", "vendor@acme.com",
         "Mon, 1 Jan 2026", "invoice.pdf",
         json.dumps(extracted), vendor_id, vendor_name,
         datetime.datetime.now(datetime.timezone.utc).isoformat()),
    )
    conn.commit()
    return inv_id


def test_approve_invoice_preview_returns_no_bill(tmp_db):
    """approve_invoice with user_confirmed=False returns preview, no bill created."""
    conn = invoice_scanner._get_conn()
    inv_id = _insert_pending_invoice(conn)
    conn.close()

    with patch("qbo_client.create_bill") as mock_create:
        result = invoice_scanner.approve_invoice(
            invoice_queue_id=inv_id,
            expense_account_id="99",
            user_confirmed=False,
        )

    mock_create.assert_not_called()
    assert result["status"] == "preview"
    assert result["vendor_name"] == "Acme Corp"


def test_approve_invoice_creates_bill_when_confirmed(tmp_db):
    """approve_invoice with user_confirmed=True calls create_bill and updates status."""
    conn = invoice_scanner._get_conn()
    inv_id = _insert_pending_invoice(conn)
    conn.close()

    fake_bill = {"Id": "999", "TotalAmt": 500.0}
    with patch("invoice_scanner.qbo_client.create_bill", return_value=fake_bill):
        result = invoice_scanner.approve_invoice(
            invoice_queue_id=inv_id,
            expense_account_id="99",
            user_confirmed=True,
        )

    assert result["status"] == "created"
    assert result["bill_id"] == "999"

    # Verify DB row updated
    conn = invoice_scanner._get_conn()
    row = conn.execute("SELECT status, bill_id FROM email_invoices WHERE id = ?", (inv_id,)).fetchone()
    conn.close()
    assert row["status"] == "created"
    assert row["bill_id"] == "999"


def test_approve_invoice_rejects_missing_vendor(tmp_db):
    """approve_invoice returns error when vendor_id is NULL in the queue."""
    conn = invoice_scanner._get_conn()
    inv_id = _insert_pending_invoice(conn, vendor_id=None, vendor_name="Unknown Co")
    conn.close()

    result = invoice_scanner.approve_invoice(
        invoice_queue_id=inv_id,
        expense_account_id="99",
        user_confirmed=True,
    )

    assert "error" in result
    assert result.get("action_required") == "add_vendor"


def test_approve_invoice_not_found(tmp_db):
    """approve_invoice returns error for unknown queue ID."""
    result = invoice_scanner.approve_invoice(
        invoice_queue_id="nonexistent-id",
        expense_account_id="99",
        user_confirmed=False,
    )
    assert "error" in result


# ---------------------------------------------------------------------------
# reject_invoice
# ---------------------------------------------------------------------------

def test_reject_invoice_marks_rejected(tmp_db):
    """reject_invoice updates status to 'rejected'."""
    conn = invoice_scanner._get_conn()
    inv_id = _insert_pending_invoice(conn)
    conn.close()

    result = invoice_scanner.reject_invoice(inv_id, reason="Duplicate")
    assert result["status"] == "rejected"

    conn = invoice_scanner._get_conn()
    row = conn.execute("SELECT status FROM email_invoices WHERE id = ?", (inv_id,)).fetchone()
    conn.close()
    assert row["status"] == "rejected"


def test_reject_invoice_cannot_reject_created(tmp_db):
    """reject_invoice returns error when invoice has already been billed."""
    conn = invoice_scanner._get_conn()
    inv_id = _insert_pending_invoice(conn)
    conn.execute("UPDATE email_invoices SET status = 'created', bill_id = '1' WHERE id = ?", (inv_id,))
    conn.commit()
    conn.close()

    result = invoice_scanner.reject_invoice(inv_id)
    assert "error" in result


def test_reject_invoice_not_found(tmp_db):
    """reject_invoice returns error for unknown queue ID."""
    result = invoice_scanner.reject_invoice("bad-id")
    assert "error" in result


# ---------------------------------------------------------------------------
# scan_emails_for_invoices — end-to-end flow with mocked Gmail + LLM
# ---------------------------------------------------------------------------


def _email_meta(message_id: str, subject: str = "Invoice", has_attachment: bool = True) -> dict:
    return {
        "message_id": message_id,
        "subject": subject,
        "from_address": "vendor@acme.com",
        "received_at": "Mon, 1 Jan 2026",
        "has_attachment": has_attachment,
        "snippet": "Please find attached.",
    }


def _parsed_invoice(vendor: str = "Acme Corp", total: float = 250.0) -> dict:
    return {
        "vendor_name": vendor,
        "invoice_number": "INV-1",
        "invoice_date": "2026-04-01",
        "due_date": "2026-04-30",
        "line_items": [{"description": "Service", "amount": total}],
        "total_amount": total,
        "currency": "USD",
        "raw_text": "",
    }


def test_scan_emails_processes_attachment_and_creates_queue_row(tmp_db):
    """End-to-end: fetch email, parse attachment, match vendor, write queue row."""
    fake_email = _email_meta("m1")
    fake_att = {"filename": "inv.pdf", "mime_type": "application/pdf", "data_b64": "ZmFrZQ=="}

    with patch("email_client.fetch_invoice_emails", return_value=[fake_email]), \
         patch("email_client.get_attachments", return_value=[fake_att]), \
         patch("email_client.mark_as_processed") as mock_mark, \
         patch("invoice_parser.parse_invoice", return_value=_parsed_invoice()), \
         patch("invoice_scanner.qbo_client.find_vendor_by_name",
               return_value={"Id": "10", "DisplayName": "Acme Corp"}):

        result = invoice_scanner.scan_emails_for_invoices(max_emails=5)

    assert result["emails_scanned"] == 1
    assert result["invoices_added"] == 1
    assert result["invoices_skipped"] == 0
    assert result["items"][0]["vendor_matched"] is True
    assert result["items"][0]["total_amount"] == 250.0
    mock_mark.assert_called_once_with("m1")

    # Verify queue row exists with matched vendor
    queue = invoice_scanner.get_invoice_queue()
    assert len(queue) == 1
    assert queue[0]["vendor_id"] == "10"
    assert queue[0]["vendor_name"] == "Acme Corp"


def test_scan_emails_skips_already_queued_message(tmp_db):
    """scan_emails_for_invoices increments invoices_skipped for duplicate email_ids."""
    # Pre-seed the queue with an existing row for m-dup
    conn = invoice_scanner._get_conn()
    _insert_pending_invoice(conn)
    conn.execute("UPDATE email_invoices SET email_id = 'm-dup'")
    conn.commit()
    conn.close()

    fake_email = _email_meta("m-dup")

    with patch("email_client.fetch_invoice_emails", return_value=[fake_email]), \
         patch("email_client.get_attachments") as mock_att, \
         patch("email_client.mark_as_processed") as mock_mark, \
         patch("invoice_parser.parse_invoice") as mock_parse:

        result = invoice_scanner.scan_emails_for_invoices()

    assert result["invoices_added"] == 0
    assert result["invoices_skipped"] == 1
    mock_att.assert_not_called()
    mock_parse.assert_not_called()
    mock_mark.assert_not_called()


def test_scan_emails_falls_back_to_body_when_no_attachment(tmp_db):
    """When has_attachment is False, scanner parses the email body text instead."""
    fake_email = _email_meta("m-body", has_attachment=False)

    with patch("email_client.fetch_invoice_emails", return_value=[fake_email]), \
         patch("email_client.get_attachments") as mock_att, \
         patch("email_client.get_email_body", return_value="Invoice total: $300"), \
         patch("email_client.mark_as_processed"), \
         patch("invoice_parser.parse_invoice", return_value=_parsed_invoice(total=300.0)) as mock_parse, \
         patch("invoice_scanner.qbo_client.find_vendor_by_name",
               return_value={"Id": "11", "DisplayName": "Acme Corp"}):

        result = invoice_scanner.scan_emails_for_invoices()

    mock_att.assert_not_called()
    mock_parse.assert_called_once()
    # parse_invoice received the text body
    assert mock_parse.call_args.kwargs["mime_type"] == "text/plain"
    assert mock_parse.call_args.kwargs["content"] == "Invoice total: $300"
    assert result["invoices_added"] == 1


def test_scan_emails_records_unmatched_vendor(tmp_db):
    """When vendor isn't in QBO, queue row stores vendor_id=None, vendor_name from LLM."""
    fake_email = _email_meta("m-noven")
    fake_att = {"filename": "inv.pdf", "mime_type": "application/pdf", "data_b64": "ZmFrZQ=="}

    with patch("email_client.fetch_invoice_emails", return_value=[fake_email]), \
         patch("email_client.get_attachments", return_value=[fake_att]), \
         patch("email_client.mark_as_processed"), \
         patch("invoice_parser.parse_invoice", return_value=_parsed_invoice(vendor="Unknown Co")), \
         patch("invoice_scanner.qbo_client.find_vendor_by_name", return_value=None):

        result = invoice_scanner.scan_emails_for_invoices()

    assert result["invoices_added"] == 1
    assert result["items"][0]["vendor_matched"] is False

    queue = invoice_scanner.get_invoice_queue()
    assert queue[0]["vendor_id"] is None
    assert queue[0]["vendor_name"] == "Unknown Co"


def test_scan_emails_returns_zero_when_inbox_empty(tmp_db):
    """No emails → no rows added, no skips."""
    with patch("email_client.fetch_invoice_emails", return_value=[]):
        result = invoice_scanner.scan_emails_for_invoices()

    assert result == {
        "emails_scanned": 0,
        "invoices_found": 0,
        "invoices_added": 0,
        "invoices_skipped": 0,
        "items": [],
    }
