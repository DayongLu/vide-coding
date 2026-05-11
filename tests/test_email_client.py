"""Tests for src/email_client.py — Gmail invoice fetching."""

import base64
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import email_client


# ---------------------------------------------------------------------------
# Helpers to build fake Gmail API responses
# ---------------------------------------------------------------------------


def _build_service_mock():
    """Return a chained-MagicMock that mimics the Gmail Resource interface."""
    service = MagicMock()
    return service


def _header(name: str, value: str) -> dict:
    return {"name": name, "value": value}


def _msg_with_subject(subject: str, parts: list | None = None) -> dict:
    payload: dict = {"headers": [_header("Subject", subject), _header("From", "a@b.com"), _header("Date", "Mon, 1 Jan 2026")]}
    if parts is not None:
        payload["parts"] = parts
    return {"payload": payload, "snippet": "snippet text"}


# ---------------------------------------------------------------------------
# _looks_like_invoice / _has_invoice_attachment
# ---------------------------------------------------------------------------


def test_looks_like_invoice_matches_subject_keyword():
    """Subject containing 'invoice' triggers a match even without attachments."""
    msg = _msg_with_subject("Your invoice for March")
    assert email_client._looks_like_invoice(msg) is True


def test_looks_like_invoice_matches_attachment():
    """A PDF attachment triggers a match even with a neutral subject."""
    msg = _msg_with_subject(
        "Hello",
        parts=[{"mimeType": "application/pdf", "filename": "doc.pdf"}],
    )
    assert email_client._looks_like_invoice(msg) is True


def test_looks_like_invoice_returns_false_for_unrelated():
    """Unrelated subject and no invoice-type attachments → False."""
    msg = _msg_with_subject("Lunch tomorrow?", parts=[{"mimeType": "text/plain"}])
    assert email_client._looks_like_invoice(msg) is False


def test_has_invoice_attachment_finds_nested_pdf():
    """_has_invoice_attachment recurses into multipart parts."""
    parts = [
        {"mimeType": "text/plain"},
        {"mimeType": "multipart/mixed", "parts": [
            {"mimeType": "application/pdf", "filename": "inv.pdf"},
        ]},
    ]
    assert email_client._has_invoice_attachment(parts) is True


def test_has_invoice_attachment_ignores_pdf_without_filename():
    """Inline parts without filenames don't count as attachments."""
    parts = [{"mimeType": "application/pdf", "filename": ""}]
    assert email_client._has_invoice_attachment(parts) is False


# ---------------------------------------------------------------------------
# fetch_invoice_emails
# ---------------------------------------------------------------------------


def test_fetch_invoice_emails_filters_non_invoice():
    """fetch_invoice_emails returns only messages that look like invoices."""
    service = _build_service_mock()
    service.users().messages().list().execute.return_value = {
        "messages": [{"id": "m1"}, {"id": "m2"}]
    }
    service.users().messages().get.side_effect = [
        # m1: invoice subject → included
        MagicMock(execute=MagicMock(return_value=_msg_with_subject(
            "Invoice from Acme",
            parts=[{"mimeType": "application/pdf", "filename": "i.pdf"}],
        ))),
        # m2: lunch → excluded
        MagicMock(execute=MagicMock(return_value=_msg_with_subject(
            "Lunch",
            parts=[{"mimeType": "text/plain"}],
        ))),
    ]

    with patch("email_client._get_service", return_value=service):
        results = email_client.fetch_invoice_emails(max_results=10)

    assert len(results) == 1
    assert results[0]["message_id"] == "m1"
    assert results[0]["has_attachment"] is True
    assert results[0]["subject"] == "Invoice from Acme"


def test_fetch_invoice_emails_returns_empty_when_no_messages():
    """fetch_invoice_emails handles empty Gmail search response."""
    service = _build_service_mock()
    service.users().messages().list().execute.return_value = {}

    with patch("email_client._get_service", return_value=service):
        results = email_client.fetch_invoice_emails()

    assert results == []


# ---------------------------------------------------------------------------
# get_attachments
# ---------------------------------------------------------------------------


def test_get_attachments_fetches_pdf_via_attachment_id():
    """get_attachments downloads an out-of-line attachment by ID."""
    service = _build_service_mock()
    service.users().messages().get().execute.return_value = {
        "payload": {
            "parts": [
                {
                    "mimeType": "application/pdf",
                    "filename": "invoice.pdf",
                    "body": {"attachmentId": "att-1"},
                }
            ]
        }
    }
    service.users().messages().attachments().get().execute.return_value = {
        "data": base64.urlsafe_b64encode(b"PDF-BYTES").decode()
    }

    with patch("email_client._get_service", return_value=service):
        results = email_client.get_attachments("msg-1")

    assert len(results) == 1
    assert results[0]["filename"] == "invoice.pdf"
    assert results[0]["mime_type"] == "application/pdf"
    # Data is normalized to standard base64
    decoded = base64.b64decode(results[0]["data_b64"] + "==")
    assert decoded == b"PDF-BYTES"


def test_get_attachments_handles_inline_data():
    """get_attachments returns inline body data when no attachmentId is present."""
    service = _build_service_mock()
    inline_b64 = base64.urlsafe_b64encode(b"PNG-INLINE").decode()
    service.users().messages().get().execute.return_value = {
        "payload": {
            "parts": [
                {
                    "mimeType": "image/png",
                    "filename": "scan.png",
                    "body": {"data": inline_b64},
                }
            ]
        }
    }

    with patch("email_client._get_service", return_value=service):
        results = email_client.get_attachments("msg-2")

    assert len(results) == 1
    assert results[0]["filename"] == "scan.png"
    assert base64.b64decode(results[0]["data_b64"] + "==") == b"PNG-INLINE"


def test_get_attachments_ignores_non_invoice_mime_types():
    """get_attachments skips parts whose MIME type is not in the invoice set."""
    service = _build_service_mock()
    service.users().messages().get().execute.return_value = {
        "payload": {
            "parts": [
                {"mimeType": "text/calendar", "filename": "event.ics",
                 "body": {"attachmentId": "att-x"}},
            ]
        }
    }

    with patch("email_client._get_service", return_value=service):
        results = email_client.get_attachments("msg-3")

    assert results == []


# ---------------------------------------------------------------------------
# get_email_body
# ---------------------------------------------------------------------------


def test_get_email_body_returns_plain_text():
    """get_email_body decodes a text/plain MIME part."""
    service = _build_service_mock()
    body_data = base64.urlsafe_b64encode(b"Hello world").decode()
    service.users().messages().get().execute.return_value = {
        "payload": {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": body_data}},
                {"mimeType": "text/html", "body": {"data": ""}},
            ],
        }
    }

    with patch("email_client._get_service", return_value=service):
        body = email_client.get_email_body("msg-4")

    assert "Hello world" in body


def test_get_email_body_falls_back_to_html_when_no_plain():
    """get_email_body strips HTML tags when only text/html is present."""
    service = _build_service_mock()
    html_data = base64.urlsafe_b64encode(b"<p>Invoice <b>$100</b></p>").decode()
    service.users().messages().get().execute.return_value = {
        "payload": {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/html", "body": {"data": html_data}},
            ],
        }
    }

    with patch("email_client._get_service", return_value=service):
        body = email_client.get_email_body("msg-5")

    assert "Invoice" in body
    assert "$100" in body
    assert "<p>" not in body


# ---------------------------------------------------------------------------
# mark_as_processed / _get_or_create_label
# ---------------------------------------------------------------------------


def test_mark_as_processed_adds_label_and_removes_unread():
    """mark_as_processed modifies the message labels via the Gmail API."""
    service = _build_service_mock()
    # _get_or_create_label finds an existing label
    service.users().labels().list().execute.return_value = {
        "labels": [{"id": "Label_1", "name": email_client.PROCESSED_LABEL_NAME}]
    }
    modify_call = service.users().messages().modify

    with patch("email_client._get_service", return_value=service):
        email_client.mark_as_processed("msg-6")

    modify_call.assert_called()
    body = modify_call.call_args.kwargs["body"]
    assert "Label_1" in body["addLabelIds"]
    assert "UNREAD" in body["removeLabelIds"]


def test_get_or_create_label_creates_when_missing():
    """_get_or_create_label calls the create endpoint when label is absent."""
    service = _build_service_mock()
    service.users().labels().list().execute.return_value = {"labels": []}
    service.users().labels().create().execute.return_value = {"id": "Label_NEW"}

    label_id = email_client._get_or_create_label(service, "InvoiceProcessed")

    assert label_id == "Label_NEW"
