"""
QuickBooks Online API client - handles all QBO queries and write operations.
"""

import datetime
import json
import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

ENVIRONMENT = os.getenv("QBO_ENVIRONMENT", "sandbox")
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "tokens.json")

if ENVIRONMENT == "sandbox":
    BASE_URL = "https://sandbox-quickbooks.api.intuit.com"
else:
    BASE_URL = "https://quickbooks.api.intuit.com"

# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class BillNotFoundError(Exception):
    """Raised when a bill ID does not exist in QBO."""


class BillAlreadyPaidError(Exception):
    """Raised when a bill has a zero balance (already fully paid)."""


class InvalidPaymentAccountError(Exception):
    """Raised when the given account ID is not found or is not a Bank account."""


class AmountExceedsBalanceError(Exception):
    """Raised when the requested payment amount exceeds the bill's remaining balance."""


class PaymentDateOutOfRangeError(Exception):
    """Raised when the payment date is outside the allowed [-90, +30] day window."""


class DuplicatePaymentError(Exception):
    """Raised when a payment against the same bill was made within the past 24 hours."""


class QBOAPIError(Exception):
    """Raised when QBO returns an unexpected HTTP error on a write operation."""


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def load_tokens() -> dict:
    """Load OAuth tokens from the local token file.

    Returns:
        dict containing access_token, refresh_token, and realm_id.
    """
    with open(TOKEN_FILE) as f:
        return json.load(f)


def qbo_request(endpoint: str, tokens: dict | None = None) -> dict:
    """Make a GET request to the QBO API.

    Args:
        endpoint: QBO API endpoint path (relative to /v3/company/{realm_id}/).
        tokens: Optional OAuth token dict. Loaded from TOKEN_FILE if None.

    Returns:
        Parsed JSON response dict.

    Raises:
        requests.HTTPError: On non-2xx responses.
    """
    if tokens is None:
        tokens = load_tokens()
    realm_id = tokens["realm_id"]
    url = f"{BASE_URL}/v3/company/{realm_id}/{endpoint}"
    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {tokens['access_token']}",
            "Accept": "application/json",
        },
    )
    response.raise_for_status()
    return response.json()


def qbo_post(endpoint: str, payload: dict, tokens: dict | None = None) -> dict:
    """Make a POST request to the QBO API.

    Args:
        endpoint: QBO API endpoint path (relative to /v3/company/{realm_id}/).
        payload: Request body to serialize as JSON.
        tokens: Optional OAuth token dict. Loaded from TOKEN_FILE if None.

    Returns:
        Parsed JSON response dict.

    Raises:
        requests.HTTPError: On non-2xx responses.
    """
    if tokens is None:
        tokens = load_tokens()
    realm_id = tokens["realm_id"]
    url = f"{BASE_URL}/v3/company/{realm_id}/{endpoint}"
    response = requests.post(
        url,
        json=payload,
        headers={
            "Authorization": f"Bearer {tokens['access_token']}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# Read-only query functions
# ---------------------------------------------------------------------------


def query(sql: str, tokens: dict | None = None) -> dict:
    """Run a QBO query using SQL-like syntax.

    Args:
        sql: QBO SQL query string.
        tokens: Optional OAuth token dict.

    Returns:
        Parsed JSON response dict.
    """
    return qbo_request(f"query?query={sql}", tokens)


def get_company_info(tokens: dict | None = None) -> dict:
    """Get company information from QBO.

    Args:
        tokens: Optional OAuth token dict.

    Returns:
        CompanyInfo dict.
    """
    if tokens is None:
        tokens = load_tokens()
    realm_id = tokens["realm_id"]
    data = qbo_request(f"companyinfo/{realm_id}", tokens)
    return data["CompanyInfo"]


def get_vendors(max_results: int = 100, tokens: dict | None = None) -> list:
    """Get a list of vendors from QBO.

    Args:
        max_results: Maximum number of vendors to return.
        tokens: Optional OAuth token dict.

    Returns:
        List of Vendor dicts.
    """
    data = query(f"SELECT * FROM Vendor MAXRESULTS {max_results}", tokens)
    return data.get("QueryResponse", {}).get("Vendor", [])


def get_bills(max_results: int = 100, tokens: dict | None = None) -> list:
    """Get all bills from QBO.

    Args:
        max_results: Maximum number of bills to return.
        tokens: Optional OAuth token dict.

    Returns:
        List of Bill dicts.
    """
    data = query(f"SELECT * FROM Bill MAXRESULTS {max_results}", tokens)
    return data.get("QueryResponse", {}).get("Bill", [])


def get_unpaid_bills(tokens: dict | None = None) -> list:
    """Get bills with a remaining balance greater than zero.

    Args:
        tokens: Optional OAuth token dict.

    Returns:
        List of unpaid Bill dicts.
    """
    data = query("SELECT * FROM Bill WHERE Balance > '0' MAXRESULTS 100", tokens)
    return data.get("QueryResponse", {}).get("Bill", [])


def get_bill_payments(max_results: int = 50, tokens: dict | None = None) -> list:
    """Get bill payment records from QBO.

    Args:
        max_results: Maximum number of payment records to return.
        tokens: Optional OAuth token dict.

    Returns:
        List of BillPayment dicts.
    """
    data = query(f"SELECT * FROM BillPayment MAXRESULTS {max_results}", tokens)
    return data.get("QueryResponse", {}).get("BillPayment", [])


def get_accounts(
    account_type: str | None = None,
    max_results: int = 100,
    tokens: dict | None = None,
) -> list:
    """Get chart-of-accounts entries from QBO, optionally filtered by type.

    Args:
        account_type: Optional QBO account type string (e.g. "Bank", "Expense").
        max_results: Maximum number of accounts to return.
        tokens: Optional OAuth token dict.

    Returns:
        List of Account dicts.
    """
    if account_type:
        sql = f"SELECT * FROM Account WHERE AccountType = '{account_type}' MAXRESULTS {max_results}"
    else:
        sql = f"SELECT * FROM Account MAXRESULTS {max_results}"
    data = query(sql, tokens)
    return data.get("QueryResponse", {}).get("Account", [])


def get_invoices(max_results: int = 50, tokens: dict | None = None) -> list:
    """Get invoices (accounts receivable) from QBO.

    Args:
        max_results: Maximum number of invoices to return.
        tokens: Optional OAuth token dict.

    Returns:
        List of Invoice dicts.
    """
    data = query(f"SELECT * FROM Invoice MAXRESULTS {max_results}", tokens)
    return data.get("QueryResponse", {}).get("Invoice", [])


def get_customers(max_results: int = 100, tokens: dict | None = None) -> list:
    """Get customers from QBO.

    Args:
        max_results: Maximum number of customers to return.
        tokens: Optional OAuth token dict.

    Returns:
        List of Customer dicts.
    """
    data = query(f"SELECT * FROM Customer MAXRESULTS {max_results}", tokens)
    return data.get("QueryResponse", {}).get("Customer", [])


def get_profit_and_loss(tokens: dict | None = None) -> dict:
    """Get the Profit and Loss report from QBO.

    Args:
        tokens: Optional OAuth token dict.

    Returns:
        P&L report dict.
    """
    if tokens is None:
        tokens = load_tokens()
    return qbo_request("reports/ProfitAndLoss", tokens)


def get_balance_sheet(tokens: dict | None = None) -> dict:
    """Get the Balance Sheet report from QBO.

    Args:
        tokens: Optional OAuth token dict.

    Returns:
        Balance Sheet report dict.
    """
    if tokens is None:
        tokens = load_tokens()
    return qbo_request("reports/BalanceSheet", tokens)


# ---------------------------------------------------------------------------
# Bill lookup
# ---------------------------------------------------------------------------


def get_bill_by_id(bill_id: str, tokens: dict | None = None) -> dict:
    """Fetch a single bill by its QBO ID.

    Args:
        bill_id: Numeric string QBO bill ID.
        tokens: Optional OAuth token dict.

    Returns:
        Bill dict from QBO.

    Raises:
        ValueError: With message "BILL_NOT_FOUND" if the ID is non-numeric or
            QBO returns a 400 response.
    """
    if not bill_id.isdigit():
        raise ValueError("BILL_NOT_FOUND")
    try:
        data = qbo_request(f"bill/{bill_id}", tokens)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 400:
            raise ValueError("BILL_NOT_FOUND") from exc
        raise
    return data["Bill"]


# ---------------------------------------------------------------------------
# Payment preview
# ---------------------------------------------------------------------------


def preview_bill_payment(
    bill_id: str,
    payment_account_id: str,
    amount: float | None = None,
    payment_date: str | None = None,
    memo: str = "",
    tokens: dict | None = None,
) -> dict:
    """Validate a proposed bill payment and return a preview summary.

    No write operations are performed. The returned dict is intended to be
    stored in a confirmation token and displayed to the user for review.

    Args:
        bill_id: Numeric string QBO bill ID.
        payment_account_id: Numeric string QBO account ID (must be a Bank account).
        amount: Payment amount. Defaults to the full bill balance if None.
        payment_date: ISO 8601 date string (YYYY-MM-DD). Defaults to today.
        memo: Optional memo / private note (max 4000 chars).
        tokens: Optional OAuth token dict.

    Returns:
        dict with keys: bill_id, vendor_name, vendor_id, bill_total,
        bill_balance, payment_amount, payment_date, payment_account_id,
        payment_account_name, payment_account_balance, balance_after_payment,
        memo, is_partial_payment, warnings.

    Raises:
        BillNotFoundError: Bill ID does not exist.
        BillAlreadyPaidError: Bill balance is zero.
        AmountExceedsBalanceError: Requested amount exceeds remaining balance.
        InvalidPaymentAccountError: Account not found or not a Bank account.
        PaymentDateOutOfRangeError: Date outside [-90, +30] day window.
        ValueError: Amount is zero or negative.
    """
    # 1. Fetch bill
    try:
        bill = get_bill_by_id(bill_id, tokens)
    except ValueError as exc:
        raise BillNotFoundError(str(exc)) from exc

    # 2. Check balance > 0
    bill_balance = float(bill.get("Balance", 0))
    if bill_balance <= 0:
        raise BillAlreadyPaidError("BILL_ALREADY_PAID")

    bill_total = float(bill.get("TotalAmt", 0))
    vendor_ref = bill.get("VendorRef", {})
    vendor_id = vendor_ref.get("value", "")
    vendor_name = vendor_ref.get("name", "")

    # 3. Validate amount
    if amount is not None:
        if amount <= 0:
            raise ValueError("Payment amount must be greater than zero.")
        if amount > bill_balance:
            raise AmountExceedsBalanceError("AMOUNT_EXCEEDS_BALANCE")
    else:
        amount = bill_balance

    # 4. Resolve payment account (must be Bank type)
    bank_accounts = get_accounts("Bank", tokens=tokens)
    matching_account = next(
        (a for a in bank_accounts if a.get("Id") == payment_account_id), None
    )
    if matching_account is None:
        raise InvalidPaymentAccountError("INVALID_PAYMENT_ACCOUNT")

    payment_account_name = matching_account.get("Name", "")
    payment_account_balance = float(matching_account.get("CurrentBalance", 0))

    # 5. Resolve and validate payment date
    today = datetime.date.today()
    if payment_date is None:
        payment_date = today.isoformat()
    try:
        parsed_date = datetime.date.fromisoformat(payment_date)
    except ValueError as exc:
        raise PaymentDateOutOfRangeError("PAYMENT_DATE_OUT_OF_RANGE") from exc

    delta = (parsed_date - today).days
    if delta < -90 or delta > 30:
        raise PaymentDateOutOfRangeError("PAYMENT_DATE_OUT_OF_RANGE")

    # 6. Compute warnings
    warnings: list[str] = []
    if payment_account_balance < amount:
        warnings.append("INSUFFICIENT_FUNDS")

    return {
        "bill_id": bill_id,
        "vendor_name": vendor_name,
        "vendor_id": vendor_id,
        "bill_total": bill_total,
        "bill_balance": bill_balance,
        "payment_amount": amount,
        "payment_date": payment_date,
        "payment_account_id": payment_account_id,
        "payment_account_name": payment_account_name,
        "payment_account_balance": payment_account_balance,
        "balance_after_payment": round(bill_balance - amount, 2),
        "memo": memo,
        "is_partial_payment": amount < bill_balance,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Payment execution
# ---------------------------------------------------------------------------


def create_bill_payment(payment_payload: dict, tokens: dict | None = None) -> dict:
    """Execute a bill payment in QBO.

    Performs a best-effort duplicate check before posting. The caller is
    responsible for token (confirmation token) validation; this function only
    handles QBO API interaction.

    Args:
        payment_payload: Dict produced by preview_bill_payment (and stored in a
            confirmation token). Must contain: bill_id, vendor_id,
            payment_amount, payment_date, payment_account_id, memo.
        tokens: Optional OAuth token dict.

    Returns:
        The created BillPayment dict from QBO.

    Raises:
        DuplicatePaymentError: A payment against the same bill was found within
            the past 24 hours.
        QBOAPIError: QBO returned an unexpected HTTP error.
    """
    # Duplicate detection: check last 50 payments for same bill in past 24h
    bill_id = payment_payload["bill_id"]
    existing_payments = get_bill_payments(tokens=tokens)
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24)
    for pmt in existing_payments:
        txn_date_str = pmt.get("TxnDate", "")
        try:
            txn_date = datetime.datetime.strptime(txn_date_str, "%Y-%m-%d").replace(
                tzinfo=datetime.timezone.utc
            )
        except ValueError:
            continue
        if txn_date < cutoff:
            continue
        for line in pmt.get("Line", []):
            for linked in line.get("LinkedTxn", []):
                if (
                    linked.get("TxnType") == "Bill"
                    and linked.get("TxnId") == bill_id
                ):
                    raise DuplicatePaymentError("DUPLICATE_PAYMENT")

    body = {
        "PayType": "Check",
        "VendorRef": {"value": payment_payload["vendor_id"]},
        "TotalAmt": payment_payload["payment_amount"],
        "TxnDate": payment_payload["payment_date"],
        "CheckPayment": {
            "BankAccountRef": {"value": payment_payload["payment_account_id"]}
        },
        "PrivateNote": payment_payload.get("memo", ""),
        "Line": [
            {
                "Amount": payment_payload["payment_amount"],
                "LinkedTxn": [
                    {"TxnId": payment_payload["bill_id"], "TxnType": "Bill"}
                ],
            }
        ],
    }

    logger.info(
        "Creating bill payment: bill_id=%s vendor_id=%s amount=%s date=%s account=%s",
        bill_id,
        payment_payload["vendor_id"],
        payment_payload["payment_amount"],
        payment_payload["payment_date"],
        payment_payload["payment_account_id"],
    )

    try:
        data = qbo_post("billpayment", body, tokens)
    except requests.HTTPError as exc:
        detail = ""
        if exc.response is not None:
            detail = exc.response.text
        raise QBOAPIError(detail) from exc

    result = data["BillPayment"]
    logger.info(
        "Bill payment created: payment_id=%s bill_id=%s",
        result.get("Id"),
        bill_id,
    )
    return result
