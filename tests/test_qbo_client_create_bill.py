"""Tests for qbo_client.create_bill and qbo_client.find_vendor_by_name."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import qbo_client


# ---------------------------------------------------------------------------
# find_vendor_by_name
# ---------------------------------------------------------------------------

_VENDORS = [
    {"Id": "1", "DisplayName": "Acme Corp"},
    {"Id": "2", "DisplayName": "BuildRight LLC"},
    {"Id": "3", "DisplayName": "Office Supplies Inc."},
]


def test_find_vendor_exact_match():
    """find_vendor_by_name returns vendor on exact case-insensitive match."""
    with patch("qbo_client.get_vendors", return_value=_VENDORS):
        result = qbo_client.find_vendor_by_name("acme corp")
    assert result is not None
    assert result["Id"] == "1"


def test_find_vendor_strips_suffix():
    """find_vendor_by_name matches after stripping LLC/Inc suffixes."""
    with patch("qbo_client.get_vendors", return_value=_VENDORS):
        result = qbo_client.find_vendor_by_name("BuildRight")
    assert result is not None
    assert result["Id"] == "2"


def test_find_vendor_partial_match():
    """find_vendor_by_name falls back to partial match."""
    with patch("qbo_client.get_vendors", return_value=_VENDORS):
        result = qbo_client.find_vendor_by_name("Office")
    assert result is not None
    assert result["Id"] == "3"


def test_find_vendor_not_found():
    """find_vendor_by_name returns None when no vendor matches."""
    with patch("qbo_client.get_vendors", return_value=_VENDORS):
        result = qbo_client.find_vendor_by_name("Unknown Vendor XYZ")
    assert result is None


def test_find_vendor_empty_name():
    """find_vendor_by_name returns None for empty name."""
    with patch("qbo_client.get_vendors", return_value=_VENDORS):
        result = qbo_client.find_vendor_by_name("")
    assert result is None


# ---------------------------------------------------------------------------
# create_bill
# ---------------------------------------------------------------------------

_CREATED_BILL = {
    "Bill": {
        "Id": "42",
        "VendorRef": {"value": "1"},
        "TotalAmt": 500.0,
        "DueDate": "2026-05-01",
    }
}


def _mock_tokens():
    return {"access_token": "tok", "realm_id": "123"}


def test_create_bill_posts_correct_payload():
    """create_bill sends correct JSON structure to QBO."""
    with (
        patch("qbo_client.load_tokens", return_value=_mock_tokens()),
        patch("qbo_client.requests.post") as mock_post,
    ):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _CREATED_BILL
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        result = qbo_client.create_bill(
            vendor_id="1",
            line_items=[{"description": "Consulting", "amount": 500.0}],
            due_date="2026-05-01",
            expense_account_id="99",
            invoice_number="INV-007",
        )

    assert result["Id"] == "42"
    call_json = mock_post.call_args.kwargs["json"]
    assert call_json["VendorRef"]["value"] == "1"
    assert call_json["DueDate"] == "2026-05-01"
    assert call_json["DocNumber"] == "INV-007"
    assert len(call_json["Line"]) == 1
    assert call_json["Line"][0]["Amount"] == 500.0
    assert call_json["Line"][0]["AccountBasedExpenseLineDetail"]["AccountRef"]["value"] == "99"


def test_create_bill_raises_on_empty_line_items():
    """create_bill raises ValueError when no line items are supplied."""
    with pytest.raises(ValueError, match="line item"):
        qbo_client.create_bill(
            vendor_id="1",
            line_items=[],
            due_date="2026-05-01",
            expense_account_id="99",
        )


def test_create_bill_raises_on_non_positive_amount():
    """create_bill raises ValueError when a line item amount is zero or negative."""
    with pytest.raises(ValueError, match="positive"):
        qbo_client.create_bill(
            vendor_id="1",
            line_items=[{"description": "Bad item", "amount": 0}],
            due_date="2026-05-01",
            expense_account_id="99",
        )


def test_create_bill_raises_on_http_error():
    """create_bill raises BillCreationError on QBO HTTP error."""
    import requests as req_module

    with (
        patch("qbo_client.load_tokens", return_value=_mock_tokens()),
        patch("qbo_client.requests.post") as mock_post,
    ):
        mock_resp = MagicMock()
        mock_resp.text = "Bad Request"
        mock_resp.raise_for_status.side_effect = req_module.HTTPError(response=mock_resp)
        mock_post.return_value = mock_resp

        with pytest.raises(qbo_client.BillCreationError):
            qbo_client.create_bill(
                vendor_id="1",
                line_items=[{"description": "Item", "amount": 100.0}],
                due_date="2026-05-01",
                expense_account_id="99",
            )
