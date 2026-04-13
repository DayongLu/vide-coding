"""
QuickBooks Online MCP Server.

Exposes QBO data as MCP tools so any LLM client (Claude Code, Claude Desktop,
Cursor, etc.) can query and write to QuickBooks.

Usage:
    python3.13 qbo_mcp_server.py              # stdio (default, for Claude Code)
    python3.13 qbo_mcp_server.py --transport sse  # SSE on port 8080
"""

import argparse
import json
import logging

from mcp.server.fastmcp import FastMCP

import payment_tokens
import qbo_client
from qbo_client import (
    AmountExceedsBalanceError,
    BillAlreadyPaidError,
    BillNotFoundError,
    DuplicatePaymentError,
    InvalidPaymentAccountError,
    PaymentDateOutOfRangeError,
    QBOAPIError,
)
from payment_tokens import TokenAlreadyUsedError, TokenExpiredError, TokenNotFoundError

logger = logging.getLogger(__name__)

mcp = FastMCP(
    name="QuickBooks Online",
    instructions=(
        "Query and write QuickBooks Online accounting data: vendors, bills, "
        "payments, invoices, customers, accounts, and financial reports.\n\n"
        "WRITE OPERATIONS — MANDATORY FLOW:\n"
        "Paying a bill requires two steps that must be followed in order:\n"
        "1. Call preview_bill_payment to validate the payment and receive a "
        "confirmation_token. Present the preview details to the user and ask "
        "for explicit confirmation before proceeding.\n"
        "2. Only after the user explicitly confirms, call create_bill_payment "
        "with the confirmation_token and user_confirmed=true. Never pass "
        "user_confirmed=true unless the user has genuinely acknowledged the "
        "payment details. Tokens expire after 5 minutes.\n\n"
        "Never skip the preview step or fabricate a confirmation token."
    ),
    host="127.0.0.1",
    port=8080,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _error_envelope(
    error_code: str, message: str, recoverable: bool
) -> str:
    """Serialize a standard error envelope to JSON.

    Args:
        error_code: Machine-readable error code string.
        message: Human-readable description for the LLM client.
        recoverable: True if the user/client can meaningfully retry.

    Returns:
        JSON string.
    """
    return json.dumps(
        {
            "status": "error",
            "error_code": error_code,
            "message": message,
            "recoverable": recoverable,
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# Read-only tools (unchanged from original)
# ---------------------------------------------------------------------------


@mcp.tool()
def get_company_info() -> str:
    """Get company information from QuickBooks (name, address, email, etc.)."""
    return json.dumps(qbo_client.get_company_info(), indent=2, default=str)


@mcp.tool()
def get_vendors(max_results: int = 100) -> str:
    """Get list of vendors/suppliers from QuickBooks."""
    return json.dumps(qbo_client.get_vendors(max_results), indent=2, default=str)


@mcp.tool()
def get_bills(max_results: int = 100) -> str:
    """Get all bills (invoices received from vendors / accounts payable) from QuickBooks."""
    return json.dumps(qbo_client.get_bills(max_results), indent=2, default=str)


@mcp.tool()
def get_unpaid_bills() -> str:
    """Get only unpaid bills (bills with a remaining balance > 0) from QuickBooks."""
    return json.dumps(qbo_client.get_unpaid_bills(), indent=2, default=str)


@mcp.tool()
def get_bill_payments(max_results: int = 50) -> str:
    """Get bill payment records from QuickBooks."""
    return json.dumps(qbo_client.get_bill_payments(max_results), indent=2, default=str)


@mcp.tool()
def get_accounts(account_type: str | None = None) -> str:
    """Get chart of accounts from QuickBooks. Can filter by account type (e.g., 'Expense', 'Income', 'Bank', 'Accounts Payable', 'Accounts Receivable')."""
    return json.dumps(qbo_client.get_accounts(account_type), indent=2, default=str)


@mcp.tool()
def get_invoices(max_results: int = 50) -> str:
    """Get invoices (bills sent TO customers / accounts receivable) from QuickBooks."""
    return json.dumps(qbo_client.get_invoices(max_results), indent=2, default=str)


@mcp.tool()
def get_customers(max_results: int = 100) -> str:
    """Get list of customers from QuickBooks."""
    return json.dumps(qbo_client.get_customers(max_results), indent=2, default=str)


@mcp.tool()
def get_profit_and_loss() -> str:
    """Get the Profit and Loss (Income Statement) report from QuickBooks."""
    return json.dumps(qbo_client.get_profit_and_loss(), indent=2, default=str)


@mcp.tool()
def get_balance_sheet() -> str:
    """Get the Balance Sheet report from QuickBooks."""
    return json.dumps(qbo_client.get_balance_sheet(), indent=2, default=str)


# ---------------------------------------------------------------------------
# Write tools — bill payment flow
# ---------------------------------------------------------------------------


@mcp.tool()
def get_bill_by_id(bill_id: str) -> str:
    """Fetch a single bill by its QuickBooks ID.

    Args:
        bill_id: Numeric QuickBooks bill ID (e.g. "123").

    Returns:
        JSON string: the Bill object on success, or an error envelope.
    """
    try:
        bill = qbo_client.get_bill_by_id(bill_id)
        return json.dumps(bill, indent=2, default=str)
    except (BillNotFoundError, ValueError):
        return _error_envelope(
            error_code="BILL_NOT_FOUND",
            message=f"Bill with ID '{bill_id}' was not found in QuickBooks.",
            recoverable=True,
        )


@mcp.tool()
def preview_bill_payment(
    bill_id: str,
    payment_account_id: str,
    amount: float | None = None,
    payment_date: str | None = None,
    memo: str = "",
) -> str:
    """Validate a proposed bill payment and return a preview with a confirmation token.

    This is the FIRST step of the two-step payment flow. After showing the
    preview to the user and receiving explicit confirmation, call
    create_bill_payment with the returned confirmation_token.

    Args:
        bill_id: Numeric QBO bill ID.
        payment_account_id: Numeric QBO Bank account ID to pay from.
        amount: Payment amount. Defaults to the full bill balance.
        payment_date: ISO 8601 date (YYYY-MM-DD). Defaults to today.
        memo: Optional private note (max 4000 chars).

    Returns:
        JSON string with keys: preview, validation, confirmation_token on
        success; or an error envelope on validation failure.
    """
    try:
        preview = qbo_client.preview_bill_payment(
            bill_id=bill_id,
            payment_account_id=payment_account_id,
            amount=amount,
            payment_date=payment_date,
            memo=memo,
        )
    except BillNotFoundError:
        return _error_envelope(
            error_code="BILL_NOT_FOUND",
            message=f"Bill with ID '{bill_id}' was not found in QuickBooks.",
            recoverable=True,
        )
    except BillAlreadyPaidError:
        return _error_envelope(
            error_code="BILL_ALREADY_PAID",
            message=f"Bill '{bill_id}' has already been fully paid (balance is zero).",
            recoverable=False,
        )
    except AmountExceedsBalanceError:
        return _error_envelope(
            error_code="AMOUNT_EXCEEDS_BALANCE",
            message="The requested payment amount exceeds the remaining bill balance.",
            recoverable=True,
        )
    except InvalidPaymentAccountError:
        return _error_envelope(
            error_code="INVALID_PAYMENT_ACCOUNT",
            message=(
                f"Account '{payment_account_id}' was not found or is not a Bank "
                "account. Use get_accounts with account_type='Bank' to find valid accounts."
            ),
            recoverable=True,
        )
    except PaymentDateOutOfRangeError:
        return _error_envelope(
            error_code="PAYMENT_DATE_OUT_OF_RANGE",
            message=(
                "Payment date must be within 90 days in the past or 30 days in the future."
            ),
            recoverable=True,
        )
    except ValueError as exc:
        return _error_envelope(
            error_code="INVALID_INPUT",
            message=str(exc),
            recoverable=True,
        )

    # Pop warnings before storing payload in token; return them in validation block
    warnings = preview.pop("warnings", [])

    token = payment_tokens.generate_token(preview)

    return json.dumps(
        {
            "preview": preview,
            "validation": {
                "valid": True,
                "warnings": warnings,
                "errors": [],
            },
            "confirmation_token": token,
        },
        indent=2,
        default=str,
    )


@mcp.tool()
def create_bill_payment(confirmation_token: str, user_confirmed: bool) -> str:
    """Execute a bill payment in QuickBooks using a previously generated token.

    This is the SECOND step of the two-step payment flow. Only call this after
    the user has explicitly reviewed the preview and confirmed the payment.

    Args:
        confirmation_token: Token returned by preview_bill_payment.
        user_confirmed: Must be True to proceed. Pass False to abort without
            consuming the token (the user can re-confirm later within the TTL).

    Returns:
        JSON string with status="success" and payment details on success, or
        an error envelope on failure.
    """
    if not user_confirmed:
        return _error_envelope(
            error_code="USER_NOT_CONFIRMED",
            message=(
                "Payment was not confirmed. The confirmation token remains valid. "
                "Ask the user to explicitly confirm before retrying with user_confirmed=true."
            ),
            recoverable=True,
        )

    # Consume token
    try:
        payload = payment_tokens.consume_token(confirmation_token)
    except TokenExpiredError:
        return _error_envelope(
            error_code="TOKEN_EXPIRED",
            message=(
                "The confirmation token has expired (tokens are valid for 5 minutes). "
                "Please call preview_bill_payment again to generate a new token."
            ),
            recoverable=True,
        )
    except TokenAlreadyUsedError:
        return _error_envelope(
            error_code="TOKEN_ALREADY_USED",
            message=(
                "This confirmation token has already been used. Each token can only "
                "be used once. Call preview_bill_payment again if needed."
            ),
            recoverable=False,
        )
    except TokenNotFoundError:
        return _error_envelope(
            error_code="TOKEN_NOT_FOUND",
            message=(
                "Confirmation token not found. Call preview_bill_payment first to "
                "generate a valid token."
            ),
            recoverable=True,
        )

    # Execute payment
    try:
        bill_payment = qbo_client.create_bill_payment(payload)
    except DuplicatePaymentError:
        return _error_envelope(
            error_code="DUPLICATE_PAYMENT",
            message=(
                "A payment against this bill was already recorded within the past "
                "24 hours. If this payment is intentional, run preview_bill_payment "
                "again to generate a new token."
            ),
            recoverable=True,
        )
    except QBOAPIError as exc:
        return _error_envelope(
            error_code="QBO_API_ERROR",
            message=f"QuickBooks returned an error: {exc}",
            recoverable=False,
        )

    return json.dumps(
        {
            "status": "success",
            "payment": {
                "payment_id": bill_payment.get("Id"),
                "bill_id": payload.get("bill_id"),
                "vendor_name": payload.get("vendor_name"),
                "amount_paid": payload.get("payment_amount"),
                "payment_date": payload.get("payment_date"),
                "payment_account": payload.get("payment_account_name"),
                "remaining_bill_balance": payload.get("balance_after_payment"),
                "memo": payload.get("memo", ""),
                "created_at": bill_payment.get("MetaData", {}).get("CreateTime"),
            },
            "message": (
                f"Payment of ${payload.get('payment_amount')} to "
                f"{payload.get('vendor_name')} was recorded successfully in QuickBooks "
                f"(payment ID: {bill_payment.get('Id')})."
            ),
        },
        indent=2,
        default=str,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QuickBooks Online MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport protocol (default: stdio)",
    )
    args = parser.parse_args()
    mcp.run(transport=args.transport)
