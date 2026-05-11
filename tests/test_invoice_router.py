"""Integration tests for the /invoices/* API endpoints."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


AUTH = {"Authorization": "Bearer test-key"}


@pytest.fixture(autouse=True)
def set_api_key(monkeypatch):
    monkeypatch.setenv("API_KEY", "test-key")


@pytest.fixture()
def client(tmp_path):
    """TestClient with isolated SQLite DB per test."""
    from api.main import create_app
    db_path = tmp_path / "test.db"
    app = create_app(db_path=db_path)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# POST /invoices/scan
# ---------------------------------------------------------------------------


def test_scan_returns_summary(client):
    """POST /invoices/scan returns the scanner summary dict."""
    summary = {"emails_scanned": 2, "invoices_found": 1, "invoices_added": 1,
               "invoices_skipped": 0, "items": []}
    with patch("invoice_scanner.scan_emails_for_invoices", return_value=summary):
        resp = client.post("/api/v1/invoices/scan", json={"max_emails": 5}, headers=AUTH)

    assert resp.status_code == 200
    assert resp.json() == summary


def test_scan_returns_503_when_gmail_not_configured(client):
    """Missing Gmail token file surfaces as a 503 with GMAIL_NOT_CONFIGURED."""
    with patch("invoice_scanner.scan_emails_for_invoices",
               side_effect=FileNotFoundError("gmail_tokens.json missing")):
        resp = client.post("/api/v1/invoices/scan", json={}, headers=AUTH)

    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["error_code"] == "GMAIL_NOT_CONFIGURED"
    assert detail["recoverable"] is False


def test_scan_returns_500_on_generic_error(client):
    """Unexpected exceptions during scan surface as 500 SCAN_ERROR."""
    with patch("invoice_scanner.scan_emails_for_invoices",
               side_effect=RuntimeError("boom")):
        resp = client.post("/api/v1/invoices/scan", json={}, headers=AUTH)

    assert resp.status_code == 500
    assert resp.json()["detail"]["error_code"] == "SCAN_ERROR"


def test_scan_requires_auth(client):
    """Unauthenticated requests return 401."""
    resp = client.post("/api/v1/invoices/scan", json={})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /invoices/queue
# ---------------------------------------------------------------------------


def test_queue_returns_list(client):
    """GET /invoices/queue forwards to invoice_scanner.get_invoice_queue."""
    items = [{"id": "abc", "status": "pending", "vendor_name": "Acme"}]
    with patch("invoice_scanner.get_invoice_queue", return_value=items) as mock_q:
        resp = client.get("/api/v1/invoices/queue", headers=AUTH)

    assert resp.status_code == 200
    assert resp.json() == items
    mock_q.assert_called_once_with(status=None)


def test_queue_passes_status_filter(client):
    """status query param is forwarded to get_invoice_queue."""
    with patch("invoice_scanner.get_invoice_queue", return_value=[]) as mock_q:
        client.get("/api/v1/invoices/queue?status=pending", headers=AUTH)

    mock_q.assert_called_once_with(status="pending")


# ---------------------------------------------------------------------------
# POST /invoices/{id}/approve
# ---------------------------------------------------------------------------


def test_approve_returns_preview(client):
    """approve returns scanner preview dict when not confirmed."""
    preview = {"status": "preview", "vendor_name": "Acme", "total_amount": 100.0}
    with patch("invoice_scanner.approve_invoice", return_value=preview):
        resp = client.post(
            "/api/v1/invoices/inv-1/approve",
            json={"expense_account_id": "99", "user_confirmed": False},
            headers=AUTH,
        )

    assert resp.status_code == 200
    assert resp.json() == preview


def test_approve_returns_404_when_not_found(client):
    """approve returns 404 when scanner reports invoice not found."""
    err = {"error": "Invoice queue ID 'bad' not found."}
    with patch("invoice_scanner.approve_invoice", return_value=err):
        resp = client.post(
            "/api/v1/invoices/bad/approve",
            json={"expense_account_id": "99"},
            headers=AUTH,
        )

    assert resp.status_code == 404


def test_approve_returns_400_on_other_error(client):
    """Non-not-found errors (e.g. missing vendor) return 400."""
    err = {"error": "Vendor 'X' is not in QBO", "action_required": "add_vendor"}
    with patch("invoice_scanner.approve_invoice", return_value=err):
        resp = client.post(
            "/api/v1/invoices/inv-2/approve",
            json={"expense_account_id": "99", "user_confirmed": True},
            headers=AUTH,
        )

    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /invoices/{id}/reject
# ---------------------------------------------------------------------------


def test_reject_returns_confirmation(client):
    """reject returns 200 with scanner response."""
    ok = {"status": "rejected", "invoice_queue_id": "inv-3",
          "message": "Invoice from 'Acme' has been rejected."}
    with patch("invoice_scanner.reject_invoice", return_value=ok):
        resp = client.post(
            "/api/v1/invoices/inv-3/reject",
            json={"reason": "Duplicate"},
            headers=AUTH,
        )

    assert resp.status_code == 200
    assert resp.json() == ok


def test_reject_returns_400_on_error(client):
    """reject returns 400 when scanner reports an error."""
    err = {"error": "Cannot reject an invoice that has already been converted to a bill."}
    with patch("invoice_scanner.reject_invoice", return_value=err):
        resp = client.post(
            "/api/v1/invoices/inv-4/reject",
            json={},
            headers=AUTH,
        )

    assert resp.status_code == 400
