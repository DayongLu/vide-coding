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
             "Mon, 1 Jan 2026", "", status, "{}", None, "Vendor", datetime.datetime.utcnow().isoformat()),
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
         datetime.datetime.utcnow().isoformat()),
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
