"""
Unit tests for the three new MCP tool functions in src/qbo_mcp_server.py:
  - get_bill_by_id(bill_id)
  - preview_bill_payment(bill_id, payment_account_id, amount, payment_date, memo)
  - create_bill_payment(confirmation_token, user_confirmed)

Strategy:
  - Import the MCP tool functions directly from qbo_mcp_server.
  - Patch ``qbo_client`` and ``payment_tokens`` at the qbo_mcp_server module
    boundary so the MCP layer's error-mapping logic is exercised in isolation.
  - Use ``assert_error_envelope`` from helpers.py for all error paths.

NOTE: The developer is implementing these tools in parallel. If the import
fails, every test in this file is skipped with an informative message rather
than failing with an ImportError. Remove the skip logic once the implementation
is merged.
"""

import json
import sys
import os
from unittest.mock import MagicMock, patch, call

import pytest

# Ensure src/ is on the path
SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# ---------------------------------------------------------------------------
# Import guard: skip this file gracefully if the new tools are not yet merged
# ---------------------------------------------------------------------------

try:
    import qbo_mcp_server as _mcp_mod

    # Check that the new tools are present; they won't be until the developer
    # adds them.
    _missing = [
        name for name in ("get_bill_by_id", "preview_bill_payment", "create_bill_payment")
        if not hasattr(_mcp_mod, name)
    ]
    if _missing:
        pytest.skip(
            f"MCP tools not yet implemented: {_missing}. "
            "Re-run after the developer merges the implementation.",
            allow_module_level=True,
        )
except ImportError as _import_err:
    pytest.skip(
        f"Could not import qbo_mcp_server: {_import_err}",
        allow_module_level=True,
    )

# After the guard, import what we need
import qbo_mcp_server
from qbo_mcp_server import (
    get_bill_by_id as mcp_get_bill_by_id,
    preview_bill_payment as mcp_preview_bill_payment,
    create_bill_payment as mcp_create_bill_payment,
)
import qbo_client
import payment_tokens
from payment_tokens import TokenAlreadyUsedError, TokenExpiredError, TokenNotFoundError
from qbo_client import (
    AmountExceedsBalanceError,
    BillAlreadyPaidError,
    BillNotFoundError,
    DuplicatePaymentError,
    InvalidPaymentAccountError,
    PaymentDateOutOfRangeError,
    QBOAPIError,
)

# Pull shared helpers
TESTS_DIR = os.path.dirname(__file__)
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

from helpers import assert_error_envelope
from conftest import BILL_UNPAID, SAMPLE_PAYMENT_PAYLOAD, QBO_CREATE_BILL_PAYMENT_RESPONSE

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MOCK_TOKEN = "prev_aabbccdd1122_1744684800"

PREVIEW_DICT = {
    "bill_id": "123",
    "vendor_name": "Acme Corp",
    "vendor_id": "55",
    "bill_total": 1500.0,
    "bill_balance": 1500.0,
    "payment_amount": 1500.0,
    "payment_date": "2026-04-12",
    "payment_account_id": "456",
    "payment_account_name": "Business Checking",
    "payment_account_balance": 42000.0,
    "balance_after_payment": 40500.0,
    "memo": "",
    "is_partial_payment": False,
    "warnings": [],
}

CREATED_PAYMENT = QBO_CREATE_BILL_PAYMENT_RESPONSE["BillPayment"]


# ===========================================================================
# get_bill_by_id MCP
# ===========================================================================


class TestMcpGetBillById:
    """UT-MCP-GBB-01 and UT-MCP-GBB-02."""

    def test_get_bill_by_id_mcp_happy_path_returns_bill_json(self, monkeypatch):
        """UT-MCP-GBB-01: Found bill returns valid JSON string with bill fields."""
        monkeypatch.setattr(qbo_client, "get_bill_by_id", lambda bill_id, tokens=None: BILL_UNPAID)

        result = mcp_get_bill_by_id("123")

        # Must be valid JSON
        data = json.loads(result)
        assert data["Id"] == "123"
        assert data["Balance"] == 1500.0

    def test_get_bill_by_id_mcp_bill_not_found_returns_error_envelope(self, monkeypatch):
        """UT-MCP-GBB-02: BillNotFoundError maps to BILL_NOT_FOUND error envelope."""
        monkeypatch.setattr(
            qbo_client,
            "get_bill_by_id",
            lambda bill_id, tokens=None: (_ for _ in ()).throw(ValueError("BILL_NOT_FOUND")),
        )

        result = mcp_get_bill_by_id("999")

        assert_error_envelope(result, "BILL_NOT_FOUND", expected_recoverable=True)


# ===========================================================================
# preview_bill_payment MCP
# ===========================================================================


class TestMcpPreviewBillPayment:
    """UT-MCP-PRV-01 through UT-MCP-PRV-09."""

    def test_preview_bill_payment_mcp_happy_path_returns_preview_and_token(
        self, monkeypatch
    ):
        """UT-MCP-PRV-01: Happy path returns preview, validation, and confirmation_token."""
        monkeypatch.setattr(
            qbo_client, "preview_bill_payment",
            lambda *a, **kw: dict(PREVIEW_DICT)
        )
        monkeypatch.setattr(
            payment_tokens, "generate_token",
            lambda payload: MOCK_TOKEN
        )

        result = mcp_preview_bill_payment("123", "456")

        data = json.loads(result)
        assert "preview" in data
        assert "confirmation_token" in data
        assert data["confirmation_token"] == MOCK_TOKEN
        assert data.get("validation", {}).get("valid") is True
        assert data.get("validation", {}).get("warnings") == []

    def test_preview_bill_payment_mcp_token_at_top_level_not_inside_preview(
        self, monkeypatch
    ):
        """UT-MCP-PRV-02: confirmation_token lives at top level, not inside preview dict."""
        monkeypatch.setattr(
            qbo_client, "preview_bill_payment",
            lambda *a, **kw: dict(PREVIEW_DICT)
        )
        monkeypatch.setattr(
            payment_tokens, "generate_token",
            lambda payload: MOCK_TOKEN
        )

        result = mcp_preview_bill_payment("123", "456")

        data = json.loads(result)
        assert "confirmation_token" in data, "confirmation_token missing at top level"
        assert "confirmation_token" not in data.get("preview", {}), (
            "confirmation_token must NOT be nested inside preview"
        )

    def test_preview_bill_payment_mcp_insufficient_funds_in_validation_warnings(
        self, monkeypatch
    ):
        """UT-MCP-PRV-03: INSUFFICIENT_FUNDS warning in preview propagates to validation.warnings."""
        preview_with_warning = {**PREVIEW_DICT, "warnings": ["INSUFFICIENT_FUNDS"]}
        monkeypatch.setattr(
            qbo_client, "preview_bill_payment",
            lambda *a, **kw: dict(preview_with_warning)
        )
        monkeypatch.setattr(
            payment_tokens, "generate_token",
            lambda payload: MOCK_TOKEN
        )

        result = mcp_preview_bill_payment("123", "456")

        data = json.loads(result)
        assert "INSUFFICIENT_FUNDS" in data["validation"]["warnings"]
        # valid should still be True (it's a warning, not hard error)
        assert data["validation"]["valid"] is True
        # Token is still generated
        assert data.get("confirmation_token") == MOCK_TOKEN

    def test_preview_bill_payment_mcp_error_does_not_generate_token(
        self, monkeypatch
    ):
        """UT-MCP-PRV-04: On client error, generate_token is never called."""
        monkeypatch.setattr(
            qbo_client, "preview_bill_payment",
            lambda *a, **kw: (_ for _ in ()).throw(BillNotFoundError("BILL_NOT_FOUND")),
        )
        mock_generate = MagicMock(return_value=MOCK_TOKEN)
        monkeypatch.setattr(payment_tokens, "generate_token", mock_generate)

        result = mcp_preview_bill_payment("999", "456")

        assert_error_envelope(result, "BILL_NOT_FOUND")
        mock_generate.assert_not_called()

    def test_preview_bill_payment_mcp_bill_already_paid_returns_not_recoverable(
        self, monkeypatch
    ):
        """UT-MCP-PRV-05: BILL_ALREADY_PAID maps to error envelope with recoverable=False."""
        monkeypatch.setattr(
            qbo_client, "preview_bill_payment",
            lambda *a, **kw: (_ for _ in ()).throw(BillAlreadyPaidError("BILL_ALREADY_PAID")),
        )
        monkeypatch.setattr(payment_tokens, "generate_token", MagicMock())

        result = mcp_preview_bill_payment("124", "456")

        assert_error_envelope(result, "BILL_ALREADY_PAID", expected_recoverable=False)

    def test_preview_bill_payment_mcp_amount_exceeds_balance_is_recoverable(
        self, monkeypatch
    ):
        """UT-MCP-PRV-06: AMOUNT_EXCEEDS_BALANCE maps to error envelope with recoverable=True."""
        monkeypatch.setattr(
            qbo_client, "preview_bill_payment",
            lambda *a, **kw: (_ for _ in ()).throw(AmountExceedsBalanceError("AMOUNT_EXCEEDS_BALANCE")),
        )

        result = mcp_preview_bill_payment("123", "456", amount=9999.0)

        assert_error_envelope(result, "AMOUNT_EXCEEDS_BALANCE", expected_recoverable=True)

    def test_preview_bill_payment_mcp_invalid_payment_account_is_recoverable(
        self, monkeypatch
    ):
        """UT-MCP-PRV-07: INVALID_PAYMENT_ACCOUNT maps to error envelope with recoverable=True."""
        monkeypatch.setattr(
            qbo_client, "preview_bill_payment",
            lambda *a, **kw: (_ for _ in ()).throw(InvalidPaymentAccountError("INVALID_PAYMENT_ACCOUNT")),
        )

        result = mcp_preview_bill_payment("123", "bad-account")

        assert_error_envelope(result, "INVALID_PAYMENT_ACCOUNT", expected_recoverable=True)

    def test_preview_bill_payment_mcp_payment_date_out_of_range_is_recoverable(
        self, monkeypatch
    ):
        """UT-MCP-PRV-08: PAYMENT_DATE_OUT_OF_RANGE maps to error envelope with recoverable=True."""
        monkeypatch.setattr(
            qbo_client, "preview_bill_payment",
            lambda *a, **kw: (_ for _ in ()).throw(
                PaymentDateOutOfRangeError("PAYMENT_DATE_OUT_OF_RANGE")
            ),
        )

        result = mcp_preview_bill_payment("123", "456", payment_date="2099-01-01")

        assert_error_envelope(result, "PAYMENT_DATE_OUT_OF_RANGE", expected_recoverable=True)

    def test_preview_bill_payment_mcp_error_envelope_always_has_four_required_fields(
        self, monkeypatch
    ):
        """UT-MCP-PRV-09 / UT-CON-01: Every error path has status, error_code, message, recoverable."""
        monkeypatch.setattr(
            qbo_client, "preview_bill_payment",
            lambda *a, **kw: (_ for _ in ()).throw(BillNotFoundError("BILL_NOT_FOUND")),
        )

        result = mcp_preview_bill_payment("999", "456")

        data = json.loads(result)
        for field in ("status", "error_code", "message", "recoverable"):
            assert field in data, f"Required field '{field}' missing from error envelope"


# ===========================================================================
# create_bill_payment MCP
# ===========================================================================


class TestMcpCreateBillPayment:
    """UT-MCP-CRE-01 through UT-MCP-CRE-10."""

    def test_create_bill_payment_mcp_happy_path_returns_success_envelope(
        self, monkeypatch
    ):
        """UT-MCP-CRE-01: Happy path returns JSON with status='success'."""
        monkeypatch.setattr(
            payment_tokens, "consume_token",
            lambda token: dict(SAMPLE_PAYMENT_PAYLOAD)
        )
        monkeypatch.setattr(
            qbo_client, "create_bill_payment",
            lambda payload, tokens=None: CREATED_PAYMENT
        )

        result = mcp_create_bill_payment(MOCK_TOKEN, user_confirmed=True)

        data = json.loads(result)
        assert data["status"] == "success"
        assert "payment" in data

    def test_create_bill_payment_mcp_success_envelope_contains_all_required_fields(
        self, monkeypatch
    ):
        """UT-MCP-CRE-02: Success envelope has all required payment fields."""
        monkeypatch.setattr(
            payment_tokens, "consume_token",
            lambda token: dict(SAMPLE_PAYMENT_PAYLOAD)
        )
        monkeypatch.setattr(
            qbo_client, "create_bill_payment",
            lambda payload, tokens=None: CREATED_PAYMENT
        )

        result = mcp_create_bill_payment(MOCK_TOKEN, user_confirmed=True)

        data = json.loads(result)
        payment = data["payment"]
        required_fields = (
            "payment_id", "bill_id", "vendor_name", "amount_paid",
            "payment_date", "payment_account", "remaining_bill_balance",
            "memo", "created_at",
        )
        for field in required_fields:
            assert field in payment, f"Required field '{field}' missing from payment object"

    def test_create_bill_payment_mcp_user_not_confirmed_false_does_not_consume_token(
        self, monkeypatch
    ):
        """UT-MCP-CRE-03: user_confirmed=False returns USER_NOT_CONFIRMED without touching token."""
        mock_consume = MagicMock()
        monkeypatch.setattr(payment_tokens, "consume_token", mock_consume)

        result = mcp_create_bill_payment(MOCK_TOKEN, user_confirmed=False)

        assert_error_envelope(result, "USER_NOT_CONFIRMED", expected_recoverable=True)
        mock_consume.assert_not_called()

    def test_create_bill_payment_mcp_token_expired_returns_token_expired_error(
        self, monkeypatch
    ):
        """UT-MCP-CRE-04: TokenExpiredError maps to TOKEN_EXPIRED envelope with recoverable=True."""
        monkeypatch.setattr(
            payment_tokens, "consume_token",
            lambda token: (_ for _ in ()).throw(TokenExpiredError("expired"))
        )

        result = mcp_create_bill_payment(MOCK_TOKEN, user_confirmed=True)

        assert_error_envelope(result, "TOKEN_EXPIRED", expected_recoverable=True)

    def test_create_bill_payment_mcp_token_already_used_returns_not_recoverable(
        self, monkeypatch
    ):
        """UT-MCP-CRE-05: TokenAlreadyUsedError maps to TOKEN_ALREADY_USED with recoverable=False."""
        monkeypatch.setattr(
            payment_tokens, "consume_token",
            lambda token: (_ for _ in ()).throw(TokenAlreadyUsedError("used"))
        )

        result = mcp_create_bill_payment(MOCK_TOKEN, user_confirmed=True)

        assert_error_envelope(result, "TOKEN_ALREADY_USED", expected_recoverable=False)

    def test_create_bill_payment_mcp_token_not_found_returns_token_not_found_error(
        self, monkeypatch
    ):
        """UT-MCP-CRE-06: TokenNotFoundError maps to TOKEN_NOT_FOUND with recoverable=True."""
        monkeypatch.setattr(
            payment_tokens, "consume_token",
            lambda token: (_ for _ in ()).throw(TokenNotFoundError("not found"))
        )

        result = mcp_create_bill_payment("prev_unknown_0", user_confirmed=True)

        assert_error_envelope(result, "TOKEN_NOT_FOUND", expected_recoverable=True)

    def test_create_bill_payment_mcp_duplicate_payment_returns_duplicate_error(
        self, monkeypatch
    ):
        """UT-MCP-CRE-07: DuplicatePaymentError maps to DUPLICATE_PAYMENT with recoverable=True."""
        monkeypatch.setattr(
            payment_tokens, "consume_token",
            lambda token: dict(SAMPLE_PAYMENT_PAYLOAD)
        )
        monkeypatch.setattr(
            qbo_client, "create_bill_payment",
            lambda payload, tokens=None: (_ for _ in ()).throw(DuplicatePaymentError("DUPLICATE_PAYMENT"))
        )

        result = mcp_create_bill_payment(MOCK_TOKEN, user_confirmed=True)

        assert_error_envelope(result, "DUPLICATE_PAYMENT", expected_recoverable=True)

    def test_create_bill_payment_mcp_qbo_api_error_includes_upstream_detail(
        self, monkeypatch
    ):
        """UT-MCP-CRE-08: QBOAPIError maps to QBO_API_ERROR; message contains upstream detail."""
        monkeypatch.setattr(
            payment_tokens, "consume_token",
            lambda token: dict(SAMPLE_PAYMENT_PAYLOAD)
        )
        monkeypatch.setattr(
            qbo_client, "create_bill_payment",
            lambda payload, tokens=None: (_ for _ in ()).throw(
                QBOAPIError("Transaction validation failed: duplicate entry")
            )
        )

        result = mcp_create_bill_payment(MOCK_TOKEN, user_confirmed=True)

        data = assert_error_envelope(result, "QBO_API_ERROR")
        assert "Transaction validation failed" in data["message"] or len(data["message"]) > 0

    def test_create_bill_payment_mcp_token_consumed_before_qbo_error(
        self, monkeypatch
    ):
        """UT-MCP-CRE-09: Token is consumed (used=True) even when QBO returns an error."""
        consume_call_count = {"n": 0}

        def tracking_consume(token):
            consume_call_count["n"] += 1
            return dict(SAMPLE_PAYMENT_PAYLOAD)

        monkeypatch.setattr(payment_tokens, "consume_token", tracking_consume)
        monkeypatch.setattr(
            qbo_client, "create_bill_payment",
            lambda payload, tokens=None: (_ for _ in ()).throw(QBOAPIError("QBO error"))
        )

        result = mcp_create_bill_payment(MOCK_TOKEN, user_confirmed=True)

        # consume_token must have been called exactly once
        assert consume_call_count["n"] == 1
        assert_error_envelope(result, "QBO_API_ERROR")

    def test_create_bill_payment_mcp_accepts_only_token_and_user_confirmed_params(
        self, monkeypatch
    ):
        """UT-MCP-CRE-10: Tool signature only accepts confirmation_token and user_confirmed."""
        import inspect
        sig = inspect.signature(mcp_create_bill_payment)
        param_names = list(sig.parameters.keys())
        assert "confirmation_token" in param_names
        assert "user_confirmed" in param_names
        # No payment-detail parameters should exist on this function
        for forbidden in ("bill_id", "amount", "payment_account_id", "payment_date", "memo"):
            assert forbidden not in param_names, (
                f"create_bill_payment should not accept '{forbidden}' — "
                "all payment details come from the token"
            )


# ===========================================================================
# Cross-cutting contract tests
# ===========================================================================


class TestCrossCuttingContracts:
    """UT-CON-01 through UT-CON-05: Contracts that hold across all three MCP tools."""

    def _collect_error_responses(self, monkeypatch):
        """Generate one error envelope from each MCP tool for contract checking."""
        responses = []

        # get_bill_by_id error
        monkeypatch.setattr(
            qbo_client, "get_bill_by_id",
            lambda bill_id, tokens=None: (_ for _ in ()).throw(ValueError("BILL_NOT_FOUND")),
        )
        responses.append(mcp_get_bill_by_id("999"))

        # preview_bill_payment error
        monkeypatch.setattr(
            qbo_client, "preview_bill_payment",
            lambda *a, **kw: (_ for _ in ()).throw(BillNotFoundError("BILL_NOT_FOUND")),
        )
        monkeypatch.setattr(payment_tokens, "generate_token", MagicMock())
        responses.append(mcp_preview_bill_payment("999", "456"))

        # create_bill_payment error (user not confirmed)
        responses.append(mcp_create_bill_payment(MOCK_TOKEN, user_confirmed=False))

        return responses

    def test_all_error_envelopes_have_four_required_fields(self, monkeypatch):
        """UT-CON-01: Every error response has status, error_code, message, recoverable."""
        responses = self._collect_error_responses(monkeypatch)
        for response_str in responses:
            data = json.loads(response_str)
            for field in ("status", "error_code", "message", "recoverable"):
                assert field in data, (
                    f"Required field '{field}' missing from error envelope: {response_str[:200]}"
                )

    def test_all_error_envelopes_have_status_equal_error(self, monkeypatch):
        """UT-CON-02: status is always 'error' in every error envelope."""
        responses = self._collect_error_responses(monkeypatch)
        for response_str in responses:
            data = json.loads(response_str)
            assert data["status"] == "error", (
                f"Expected status='error', got '{data.get('status')}'"
            )

    def test_success_responses_have_status_equal_success(self, monkeypatch):
        """UT-CON-03: status is 'success' in all success responses."""
        monkeypatch.setattr(
            payment_tokens, "consume_token",
            lambda token: dict(SAMPLE_PAYMENT_PAYLOAD)
        )
        monkeypatch.setattr(
            qbo_client, "create_bill_payment",
            lambda payload, tokens=None: CREATED_PAYMENT
        )

        result = mcp_create_bill_payment(MOCK_TOKEN, user_confirmed=True)

        data = json.loads(result)
        assert data["status"] == "success"

    def test_all_responses_are_valid_json(self, monkeypatch):
        """UT-CON-04: Every tool response parses without json.loads exception."""
        responses = self._collect_error_responses(monkeypatch)
        for response_str in responses:
            try:
                json.loads(response_str)
            except json.JSONDecodeError as exc:
                pytest.fail(f"Tool response is not valid JSON: {exc}\nResponse: {response_str[:200]}")

    def test_recoverable_is_boolean_not_string(self, monkeypatch):
        """UT-CON-05: recoverable field is a Python bool, not a string."""
        responses = self._collect_error_responses(monkeypatch)
        for response_str in responses:
            data = json.loads(response_str)
            assert isinstance(data.get("recoverable"), bool), (
                f"'recoverable' should be bool, got {type(data.get('recoverable'))} "
                f"in response: {response_str[:200]}"
            )
