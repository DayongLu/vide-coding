"""
Unit tests for the bill-payment functions in src/qbo_client.py.

Mocks:
  - ``requests.get`` and ``requests.post`` at the ``qbo_client`` module boundary
    for HTTP-level tests (qbo_post, get_bill_by_id).
  - ``qbo_client.get_bill_by_id`` and ``qbo_client.get_accounts`` for
    ``preview_bill_payment`` tests (higher-level mocking).
  - ``qbo_client.qbo_post`` and ``qbo_client.get_bill_payments`` for
    ``create_bill_payment`` tests.

All tests pass an explicit ``tokens`` dict to avoid loading ``tokens.json``
from disk.
"""

import datetime
import sys
import os
from unittest.mock import MagicMock, patch, call

import pytest
import requests

# Ensure src/ is importable
SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import qbo_client
from qbo_client import (
    AmountExceedsBalanceError,
    BillAlreadyPaidError,
    BillNotFoundError,
    DuplicatePaymentError,
    InvalidPaymentAccountError,
    PaymentDateOutOfRangeError,
    QBOAPIError,
    create_bill_payment,
    get_bill_by_id,
    preview_bill_payment,
    qbo_post,
)

# ---------------------------------------------------------------------------
# Shared test tokens (avoids loading tokens.json from disk)
# ---------------------------------------------------------------------------

FAKE_TOKENS = {
    "access_token": "test-access-token",
    "refresh_token": "test-refresh-token",
    "realm_id": "9991234567",
}

# ---------------------------------------------------------------------------
# Shared fixtures from conftest (re-exported names for clarity)
# ---------------------------------------------------------------------------

from conftest import (
    BANK_ACCOUNT_INSUFFICIENT,
    BANK_ACCOUNT_SUFFICIENT,
    BILL_PAID,
    BILL_UNPAID,
    EXPENSE_ACCOUNT,
    QBO_CREATE_BILL_PAYMENT_RESPONSE,
    QBO_BILL_PAYMENTS_EMPTY,
    QBO_BILL_PAYMENTS_RECENT,
    SAMPLE_PAYMENT_PAYLOAD,
)


# ---------------------------------------------------------------------------
# Helper: build a mock response with raise_for_status behaviour
# ---------------------------------------------------------------------------


def _mock_response(status_code=200, json_data=None, text=""):
    """Return a MagicMock that behaves like a requests.Response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.text = text
    if json_data is not None:
        mock_resp.json.return_value = json_data
    if status_code >= 400:
        http_error = requests.HTTPError(response=mock_resp)
        mock_resp.raise_for_status.side_effect = http_error
    else:
        mock_resp.raise_for_status.return_value = None
    return mock_resp


# ---------------------------------------------------------------------------
# Helper: make a side_effect function that raises an exception
# ---------------------------------------------------------------------------


def _raiser(exc):
    """Return a callable that raises ``exc`` when called with any arguments."""
    def _raise(*args, **kwargs):
        raise exc
    return _raise


# ===========================================================================
# qbo_post
# ===========================================================================


class TestQboPost:
    """UT-POST-01 through UT-POST-05."""

    def test_qbo_post_success_returns_parsed_json(self):
        """UT-POST-01: 200 response body is parsed and returned as dict."""
        expected = {"BillPayment": {"Id": "789"}}
        mock_resp = _mock_response(200, json_data=expected)
        with patch("qbo_client.requests.post", return_value=mock_resp):
            result = qbo_post("billpayment", {"PayType": "Check"}, tokens=FAKE_TOKENS)
        assert result == expected

    def test_qbo_post_sets_content_type_header(self):
        """UT-POST-02: The request includes Content-Type: application/json."""
        mock_resp = _mock_response(200, json_data={})
        with patch("qbo_client.requests.post", return_value=mock_resp) as mock_post:
            qbo_post("billpayment", {}, tokens=FAKE_TOKENS)
        _, kwargs = mock_post.call_args
        headers = kwargs.get("headers", {})
        assert headers.get("Content-Type") == "application/json"

    def test_qbo_post_400_response_raises_http_error(self):
        """UT-POST-03: 400 response raises requests.HTTPError."""
        mock_resp = _mock_response(400, text="Bad request")
        with patch("qbo_client.requests.post", return_value=mock_resp):
            with pytest.raises(requests.HTTPError):
                qbo_post("billpayment", {}, tokens=FAKE_TOKENS)

    def test_qbo_post_500_response_raises_http_error(self):
        """UT-POST-04: 500 response raises requests.HTTPError."""
        mock_resp = _mock_response(500, text="Internal server error")
        with patch("qbo_client.requests.post", return_value=mock_resp):
            with pytest.raises(requests.HTTPError):
                qbo_post("billpayment", {}, tokens=FAKE_TOKENS)

    def test_qbo_post_uses_provided_tokens(self):
        """UT-POST-05: Authorization header uses provided tokens; load_tokens() not called."""
        mock_resp = _mock_response(200, json_data={})
        custom_tokens = {**FAKE_TOKENS, "access_token": "custom-token-xyz"}
        with patch("qbo_client.requests.post", return_value=mock_resp) as mock_post:
            with patch("qbo_client.load_tokens") as mock_load:
                qbo_post("billpayment", {}, tokens=custom_tokens)
        mock_load.assert_not_called()
        _, kwargs = mock_post.call_args
        headers = kwargs.get("headers", {})
        assert "custom-token-xyz" in headers.get("Authorization", "")


# ===========================================================================
# get_bill_by_id
# ===========================================================================


class TestGetBillById:
    """UT-GBB-01 through UT-GBB-03."""

    def test_get_bill_by_id_valid_id_returns_bill_dict(self):
        """UT-GBB-01: Found bill — returns the Bill object dict."""
        api_response = {"Bill": BILL_UNPAID, "time": "2026-04-12T14:00:00Z"}
        mock_resp = _mock_response(200, json_data=api_response)
        with patch("qbo_client.requests.get", return_value=mock_resp):
            result = get_bill_by_id("123", tokens=FAKE_TOKENS)
        assert result == BILL_UNPAID

    def test_get_bill_by_id_not_found_raises_value_error_bill_not_found(self):
        """UT-GBB-02: QBO 400 is mapped to ValueError('BILL_NOT_FOUND')."""
        mock_resp = _mock_response(400, text='{"Fault": {"Error": [{"code": "610"}]}}')
        with patch("qbo_client.requests.get", return_value=mock_resp):
            with pytest.raises(ValueError, match="BILL_NOT_FOUND"):
                get_bill_by_id("999", tokens=FAKE_TOKENS)

    def test_get_bill_by_id_non_numeric_id_raises_value_error(self):
        """UT-GBB-03: Non-numeric ID raises ValueError before any HTTP call."""
        with patch("qbo_client.requests.get") as mock_get:
            with pytest.raises(ValueError):
                get_bill_by_id("abc", tokens=FAKE_TOKENS)
        mock_get.assert_not_called()

    def test_get_bill_by_id_non_numeric_alphanumeric_no_http(self):
        """get_bill_by_id rejects IDs like '12abc' without making HTTP calls."""
        with patch("qbo_client.requests.get") as mock_get:
            with pytest.raises(ValueError):
                get_bill_by_id("12abc", tokens=FAKE_TOKENS)
        mock_get.assert_not_called()


# ===========================================================================
# preview_bill_payment
# ===========================================================================


@pytest.fixture
def mock_bill_and_account(monkeypatch):
    """Provide happy-path mocks: unpaid bill + sufficient bank account."""
    monkeypatch.setattr(
        qbo_client, "get_bill_by_id",
        lambda bill_id, tokens=None: BILL_UNPAID
    )
    monkeypatch.setattr(
        qbo_client, "get_accounts",
        lambda account_type=None, max_results=100, tokens=None: [BANK_ACCOUNT_SUFFICIENT]
    )


@pytest.fixture
def frozen_today_fixture(monkeypatch):
    """Pin qbo_client's datetime.date.today() to 2026-04-12.

    We patch the ``datetime`` module object inside ``qbo_client`` so the
    function under test sees our fixed date, without touching the immutable
    C-level ``datetime.date`` class.
    """
    fixed_date = datetime.date(2026, 4, 12)

    # Build a replacement datetime module-like object that returns fixed_date
    # for .date.today() and passes through everything else.
    real_datetime = qbo_client.datetime

    class _FakeDatetime:
        """Minimal stand-in that exposes .date.today() and .date.fromisoformat."""

        class date:
            @staticmethod
            def today():
                return fixed_date

            @staticmethod
            def fromisoformat(s):
                return real_datetime.date.fromisoformat(s)

        # Expose timedelta and datetime for the rest of qbo_client
        timedelta = real_datetime.timedelta
        datetime = real_datetime.datetime

    monkeypatch.setattr(qbo_client, "datetime", _FakeDatetime)
    return fixed_date


class TestPreviewBillPayment:
    """UT-PRV-01 through UT-PRV-18."""

    # --- Happy paths ---

    def test_preview_bill_payment_happy_path_full_balance_returns_complete_dict(
        self, mock_bill_and_account, frozen_today_fixture
    ):
        """UT-PRV-01: Full-balance preview returns all required keys."""
        result = preview_bill_payment(
            "123", "456", tokens=FAKE_TOKENS
        )
        assert result["payment_amount"] == 1500.0
        assert result["is_partial_payment"] is False
        assert result["warnings"] == []
        # Verify all required fields are present
        for key in (
            "bill_id", "vendor_name", "vendor_id", "bill_total", "bill_balance",
            "payment_amount", "payment_date", "payment_account_id",
            "payment_account_name", "payment_account_balance",
            "balance_after_payment", "memo", "is_partial_payment", "warnings",
        ):
            assert key in result, f"Missing key '{key}' in preview result"

    def test_preview_bill_payment_partial_amount_sets_is_partial_payment_true(
        self, mock_bill_and_account, frozen_today_fixture
    ):
        """UT-PRV-02: Partial payment sets is_partial_payment=True; bill_balance unchanged."""
        result = preview_bill_payment(
            "123", "456", amount=500.0, tokens=FAKE_TOKENS
        )
        assert result["payment_amount"] == 500.0
        assert result["is_partial_payment"] is True
        assert result["bill_balance"] == 1500.0

    def test_preview_bill_payment_balance_after_payment_computed_correctly(
        self, mock_bill_and_account, frozen_today_fixture
    ):
        """UT-PRV-14: balance_after_payment = bill_balance - payment_amount."""
        result = preview_bill_payment("123", "456", tokens=FAKE_TOKENS)
        # bill_balance=1500, payment_amount=1500 (full balance), so balance_after=0
        assert result["balance_after_payment"] == pytest.approx(0.0)

    def test_preview_bill_payment_defaults_payment_date_to_today(
        self, mock_bill_and_account, frozen_today_fixture
    ):
        """UT-PRV-13: When payment_date is omitted, result uses today's date."""
        result = preview_bill_payment("123", "456", tokens=FAKE_TOKENS)
        assert result["payment_date"] == "2026-04-12"

    # --- Validation failures ---

    def test_preview_bill_payment_bill_not_found_raises_bill_not_found_error(
        self, monkeypatch, frozen_today_fixture
    ):
        """UT-PRV-03: BillNotFoundError propagated when bill absent."""
        monkeypatch.setattr(
            qbo_client, "get_bill_by_id",
            _raiser(ValueError("BILL_NOT_FOUND"))
        )
        with pytest.raises(BillNotFoundError):
            preview_bill_payment("999", "456", tokens=FAKE_TOKENS)

    def test_preview_bill_payment_balance_zero_raises_bill_already_paid_error(
        self, monkeypatch, frozen_today_fixture
    ):
        """UT-PRV-04: BillAlreadyPaidError raised when bill balance is 0."""
        monkeypatch.setattr(
            qbo_client, "get_bill_by_id",
            lambda bill_id, tokens=None: BILL_PAID
        )
        monkeypatch.setattr(
            qbo_client, "get_accounts",
            lambda account_type=None, max_results=100, tokens=None: [BANK_ACCOUNT_SUFFICIENT]
        )
        with pytest.raises(BillAlreadyPaidError):
            preview_bill_payment("124", "456", tokens=FAKE_TOKENS)

    def test_preview_bill_payment_account_not_found_raises_invalid_payment_account_error(
        self, monkeypatch, frozen_today_fixture
    ):
        """UT-PRV-05: InvalidPaymentAccountError when account ID not in Bank accounts list."""
        monkeypatch.setattr(
            qbo_client, "get_bill_by_id",
            lambda bill_id, tokens=None: BILL_UNPAID
        )
        # Return empty list — ID 999 won't be found
        monkeypatch.setattr(
            qbo_client, "get_accounts",
            lambda account_type=None, max_results=100, tokens=None: []
        )
        with pytest.raises(InvalidPaymentAccountError):
            preview_bill_payment("123", "999", tokens=FAKE_TOKENS)

    def test_preview_bill_payment_account_wrong_type_raises_invalid_payment_account_error(
        self, monkeypatch, frozen_today_fixture
    ):
        """UT-PRV-06: InvalidPaymentAccountError when account is Expense type (not Bank).

        get_accounts(account_type='Bank') returns empty list because QBO already
        filters out non-Bank accounts server-side. The Expense account would not
        appear in the Bank query results.
        """
        monkeypatch.setattr(
            qbo_client, "get_bill_by_id",
            lambda bill_id, tokens=None: BILL_UNPAID
        )
        # Bank query returns empty — expense account filtered out
        monkeypatch.setattr(
            qbo_client, "get_accounts",
            lambda account_type=None, max_results=100, tokens=None: []
        )
        with pytest.raises(InvalidPaymentAccountError):
            preview_bill_payment("123", "789", tokens=FAKE_TOKENS)

    def test_preview_bill_payment_amount_exceeds_balance_raises_error(
        self, monkeypatch, frozen_today_fixture
    ):
        """UT-PRV-07: AmountExceedsBalanceError when amount > bill balance."""
        bill = {**BILL_UNPAID, "Balance": 500.0, "TotalAmt": 500.0}
        monkeypatch.setattr(
            qbo_client, "get_bill_by_id",
            lambda bill_id, tokens=None: bill
        )
        monkeypatch.setattr(
            qbo_client, "get_accounts",
            lambda account_type=None, max_results=100, tokens=None: [BANK_ACCOUNT_SUFFICIENT]
        )
        with pytest.raises(AmountExceedsBalanceError):
            preview_bill_payment("123", "456", amount=600.0, tokens=FAKE_TOKENS)

    def test_preview_bill_payment_date_too_far_future_raises_payment_date_out_of_range(
        self, mock_bill_and_account, frozen_today_fixture
    ):
        """UT-PRV-08: PaymentDateOutOfRangeError when date is +31 days from today (2026-05-13)."""
        with pytest.raises(PaymentDateOutOfRangeError):
            preview_bill_payment(
                "123", "456", payment_date="2026-05-13", tokens=FAKE_TOKENS
            )

    def test_preview_bill_payment_date_too_far_past_raises_payment_date_out_of_range(
        self, mock_bill_and_account, frozen_today_fixture
    ):
        """UT-PRV-09: PaymentDateOutOfRangeError when date is -91 days from today (2026-01-11)."""
        with pytest.raises(PaymentDateOutOfRangeError):
            preview_bill_payment(
                "123", "456", payment_date="2026-01-11", tokens=FAKE_TOKENS
            )

    def test_preview_bill_payment_low_account_balance_adds_insufficient_funds_warning(
        self, monkeypatch, frozen_today_fixture
    ):
        """UT-PRV-10: INSUFFICIENT_FUNDS warning added (not error) when account balance < amount."""
        monkeypatch.setattr(
            qbo_client, "get_bill_by_id",
            lambda bill_id, tokens=None: BILL_UNPAID
        )
        monkeypatch.setattr(
            qbo_client, "get_accounts",
            lambda account_type=None, max_results=100, tokens=None: [BANK_ACCOUNT_INSUFFICIENT]
        )
        result = preview_bill_payment("123", "457", amount=1500.0, tokens=FAKE_TOKENS)
        # Must not raise — INSUFFICIENT_FUNDS is a warning, not an error
        assert "INSUFFICIENT_FUNDS" in result["warnings"]

    def test_preview_bill_payment_insufficient_funds_does_not_prevent_success(
        self, monkeypatch, frozen_today_fixture
    ):
        """INSUFFICIENT_FUNDS is a soft warning — the preview dict is still returned."""
        monkeypatch.setattr(
            qbo_client, "get_bill_by_id",
            lambda bill_id, tokens=None: BILL_UNPAID
        )
        monkeypatch.setattr(
            qbo_client, "get_accounts",
            lambda account_type=None, max_results=100, tokens=None: [BANK_ACCOUNT_INSUFFICIENT]
        )
        result = preview_bill_payment("123", "457", tokens=FAKE_TOKENS)
        assert result is not None
        assert result["payment_amount"] == 1500.0

    # --- Boundary date tests ---

    def test_preview_bill_payment_date_at_boundary_plus_30_is_accepted(
        self, mock_bill_and_account, frozen_today_fixture
    ):
        """UT-PRV-11: payment_date exactly +30 days from today (2026-05-12) is accepted."""
        result = preview_bill_payment(
            "123", "456", payment_date="2026-05-12", tokens=FAKE_TOKENS
        )
        assert result["payment_date"] == "2026-05-12"

    def test_preview_bill_payment_date_at_boundary_minus_90_is_accepted(
        self, mock_bill_and_account, frozen_today_fixture
    ):
        """UT-PRV-12: payment_date exactly -90 days from today (2026-01-12) is accepted."""
        result = preview_bill_payment(
            "123", "456", payment_date="2026-01-12", tokens=FAKE_TOKENS
        )
        assert result["payment_date"] == "2026-01-12"

    # --- Validation ordering tests ---

    def test_preview_bill_payment_bill_not_found_does_not_call_get_accounts(
        self, monkeypatch, frozen_today_fixture
    ):
        """UT-PRV-17: BILL_NOT_FOUND is checked before get_accounts is called."""
        monkeypatch.setattr(
            qbo_client, "get_bill_by_id",
            _raiser(ValueError("BILL_NOT_FOUND"))
        )
        mock_get_accounts = MagicMock(return_value=[BANK_ACCOUNT_SUFFICIENT])
        monkeypatch.setattr(qbo_client, "get_accounts", mock_get_accounts)

        with pytest.raises(BillNotFoundError):
            preview_bill_payment("999", "456", tokens=FAKE_TOKENS)

        mock_get_accounts.assert_not_called()

    def test_preview_bill_payment_bill_already_paid_does_not_call_get_accounts(
        self, monkeypatch, frozen_today_fixture
    ):
        """UT-PRV-18: BILL_ALREADY_PAID is checked before get_accounts is called."""
        monkeypatch.setattr(
            qbo_client, "get_bill_by_id",
            lambda bill_id, tokens=None: BILL_PAID
        )
        mock_get_accounts = MagicMock(return_value=[BANK_ACCOUNT_SUFFICIENT])
        monkeypatch.setattr(qbo_client, "get_accounts", mock_get_accounts)

        with pytest.raises(BillAlreadyPaidError):
            preview_bill_payment("124", "456", tokens=FAKE_TOKENS)

        mock_get_accounts.assert_not_called()

    # --- Amount validation ---

    def test_preview_bill_payment_zero_amount_raises_value_error(
        self, mock_bill_and_account, frozen_today_fixture
    ):
        """UT-PRV-15: amount=0 is rejected with ValueError."""
        with pytest.raises((ValueError, AmountExceedsBalanceError)):
            preview_bill_payment("123", "456", amount=0, tokens=FAKE_TOKENS)

    def test_preview_bill_payment_negative_amount_raises_value_error(
        self, mock_bill_and_account, frozen_today_fixture
    ):
        """UT-PRV-16: Negative amount is rejected with ValueError."""
        with pytest.raises((ValueError, AmountExceedsBalanceError)):
            preview_bill_payment("123", "456", amount=-50.0, tokens=FAKE_TOKENS)


# ===========================================================================
# create_bill_payment
# ===========================================================================


@pytest.fixture
def payment_payload():
    """A valid preview payload for create_bill_payment tests."""
    return dict(SAMPLE_PAYMENT_PAYLOAD)


class TestCreateBillPayment:
    """UT-CRE-01 through UT-CRE-10."""

    def test_create_bill_payment_happy_path_calls_qbo_post_and_returns_payment(
        self, payment_payload, monkeypatch
    ):
        """UT-CRE-01: Happy path returns BillPayment dict; qbo_post called once with 'billpayment'."""
        expected_payment = QBO_CREATE_BILL_PAYMENT_RESPONSE["BillPayment"]
        mock_qbo_post = MagicMock(return_value=QBO_CREATE_BILL_PAYMENT_RESPONSE)
        monkeypatch.setattr(qbo_client, "get_bill_payments", lambda **kw: [])
        monkeypatch.setattr(qbo_client, "qbo_post", mock_qbo_post)

        result = create_bill_payment(payment_payload, tokens=FAKE_TOKENS)

        assert result == expected_payment
        mock_qbo_post.assert_called_once()
        assert mock_qbo_post.call_args[0][0] == "billpayment"

    def test_create_bill_payment_qbo_body_contains_all_required_fields(
        self, payment_payload, monkeypatch
    ):
        """UT-CRE-02: Captured POST body includes all required QBO fields."""
        captured_body = {}

        def capture_post(endpoint, body, tokens=None):
            captured_body.update(body)
            return QBO_CREATE_BILL_PAYMENT_RESPONSE

        monkeypatch.setattr(qbo_client, "get_bill_payments", lambda **kw: [])
        monkeypatch.setattr(qbo_client, "qbo_post", capture_post)

        create_bill_payment(payment_payload, tokens=FAKE_TOKENS)

        assert "PayType" in captured_body
        assert "VendorRef" in captured_body
        assert "TotalAmt" in captured_body
        assert "TxnDate" in captured_body
        assert "CheckPayment" in captured_body
        assert "BankAccountRef" in captured_body["CheckPayment"]
        assert len(captured_body.get("Line", [])) > 0
        line = captured_body["Line"][0]
        assert len(line.get("LinkedTxn", [])) > 0
        assert line["LinkedTxn"][0]["TxnId"] == payment_payload["bill_id"]

    def test_create_bill_payment_line_amount_matches_total_amt(
        self, monkeypatch
    ):
        """UT-CRE-03: Line[0].Amount and TotalAmt both equal payment_amount."""
        payload = {**SAMPLE_PAYMENT_PAYLOAD, "payment_amount": 750.0, "bill_id": "123"}
        captured_body = {}

        def capture_post(endpoint, body, tokens=None):
            captured_body.update(body)
            return {"BillPayment": {"Id": "900", "TotalAmt": 750.0}}

        monkeypatch.setattr(qbo_client, "get_bill_payments", lambda **kw: [])
        monkeypatch.setattr(qbo_client, "qbo_post", capture_post)

        create_bill_payment(payload, tokens=FAKE_TOKENS)

        assert captured_body["TotalAmt"] == 750.0
        assert captured_body["Line"][0]["Amount"] == 750.0

    def test_create_bill_payment_duplicate_within_24h_raises_duplicate_payment_error(
        self, payment_payload, monkeypatch
    ):
        """UT-CRE-04: DuplicatePaymentError when same bill paid within 24h; qbo_post not called."""
        import datetime as _dt
        today = _dt.date.today().isoformat()
        recent_payment = {
            "Id": "700",
            "TxnDate": today,
            "TotalAmt": 1500.00,
            "Line": [{"LinkedTxn": [{"TxnId": "123", "TxnType": "Bill"}]}],
            "MetaData": {"CreateTime": f"{today}T10:00:00Z"},
        }
        mock_qbo_post = MagicMock()
        monkeypatch.setattr(qbo_client, "get_bill_payments", lambda **kw: [recent_payment])
        monkeypatch.setattr(qbo_client, "qbo_post", mock_qbo_post)

        with pytest.raises(DuplicatePaymentError):
            create_bill_payment(payment_payload, tokens=FAKE_TOKENS)

        mock_qbo_post.assert_not_called()

    def test_create_bill_payment_qbo_400_raises_qbo_api_error_with_detail(
        self, payment_payload, monkeypatch
    ):
        """UT-CRE-05: QBO 400 is wrapped in QBOAPIError; original detail preserved."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Duplicate transaction"
        http_error = requests.HTTPError(response=mock_resp)

        monkeypatch.setattr(qbo_client, "get_bill_payments", lambda **kw: [])
        monkeypatch.setattr(
            qbo_client, "qbo_post", _raiser(http_error)
        )

        with pytest.raises(QBOAPIError) as exc_info:
            create_bill_payment(payment_payload, tokens=FAKE_TOKENS)

        assert "Duplicate transaction" in str(exc_info.value)

    def test_create_bill_payment_qbo_500_raises_qbo_api_error(
        self, payment_payload, monkeypatch
    ):
        """UT-CRE-06: QBO 500 is wrapped in QBOAPIError."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        http_error = requests.HTTPError(response=mock_resp)

        monkeypatch.setattr(qbo_client, "get_bill_payments", lambda **kw: [])
        monkeypatch.setattr(
            qbo_client, "qbo_post", _raiser(http_error)
        )

        with pytest.raises(QBOAPIError):
            create_bill_payment(payment_payload, tokens=FAKE_TOKENS)

    def test_create_bill_payment_memo_appears_as_private_note(
        self, monkeypatch
    ):
        """UT-CRE-07: memo field is passed as PrivateNote in the POST body."""
        payload = {**SAMPLE_PAYMENT_PAYLOAD, "memo": "Q1 settlement"}
        captured_body = {}

        def capture_post(endpoint, body, tokens=None):
            captured_body.update(body)
            return QBO_CREATE_BILL_PAYMENT_RESPONSE

        monkeypatch.setattr(qbo_client, "get_bill_payments", lambda **kw: [])
        monkeypatch.setattr(qbo_client, "qbo_post", capture_post)

        create_bill_payment(payload, tokens=FAKE_TOKENS)

        assert captured_body.get("PrivateNote") == "Q1 settlement"

    def test_create_bill_payment_empty_memo_sends_empty_string_not_none(
        self, payment_payload, monkeypatch
    ):
        """UT-CRE-08: Empty memo produces PrivateNote='' in POST body, not None."""
        payload = {**payment_payload, "memo": ""}
        captured_body = {}

        def capture_post(endpoint, body, tokens=None):
            captured_body.update(body)
            return QBO_CREATE_BILL_PAYMENT_RESPONSE

        monkeypatch.setattr(qbo_client, "get_bill_payments", lambda **kw: [])
        monkeypatch.setattr(qbo_client, "qbo_post", capture_post)

        create_bill_payment(payload, tokens=FAKE_TOKENS)

        assert "PrivateNote" in captured_body
        assert captured_body["PrivateNote"] == ""
        assert captured_body["PrivateNote"] is not None

    def test_create_bill_payment_duplicate_older_than_24h_does_not_raise(
        self, payment_payload, monkeypatch
    ):
        """UT-CRE-09: Payment for the same bill but >24h ago does not trigger DuplicatePaymentError."""
        old_payment = {
            "Id": "600",
            "TxnDate": "2026-04-11",  # yesterday — outside the 24h window
            "TotalAmt": 1500.00,
            "Line": [{"LinkedTxn": [{"TxnId": "123", "TxnType": "Bill"}]}],
            "MetaData": {"CreateTime": "2026-04-11T09:00:00Z"},
        }
        mock_qbo_post = MagicMock(return_value=QBO_CREATE_BILL_PAYMENT_RESPONSE)
        monkeypatch.setattr(qbo_client, "get_bill_payments", lambda **kw: [old_payment])
        monkeypatch.setattr(qbo_client, "qbo_post", mock_qbo_post)

        result = create_bill_payment(payment_payload, tokens=FAKE_TOKENS)
        assert result is not None
        mock_qbo_post.assert_called_once()

    def test_create_bill_payment_different_bill_id_does_not_trigger_duplicate(
        self, payment_payload, monkeypatch
    ):
        """UT-CRE-10: Recent payment for a different bill ID does not raise DuplicatePaymentError."""
        other_bill_payment = {
            "Id": "701",
            "TxnDate": "2026-04-12",
            "TotalAmt": 500.00,
            "Line": [{"LinkedTxn": [{"TxnId": "999", "TxnType": "Bill"}]}],  # different bill
            "MetaData": {"CreateTime": "2026-04-12T10:00:00Z"},
        }
        mock_qbo_post = MagicMock(return_value=QBO_CREATE_BILL_PAYMENT_RESPONSE)
        monkeypatch.setattr(qbo_client, "get_bill_payments", lambda **kw: [other_bill_payment])
        monkeypatch.setattr(qbo_client, "qbo_post", mock_qbo_post)

        result = create_bill_payment(payment_payload, tokens=FAKE_TOKENS)
        assert result is not None
        mock_qbo_post.assert_called_once()
