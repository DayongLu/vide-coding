"""
Shared pytest fixtures and test data for bill payment tests.
"""

import sys
import os

import pytest

# ---------------------------------------------------------------------------
# Ensure src/ is importable as a package root
# ---------------------------------------------------------------------------

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# ---------------------------------------------------------------------------
# Bill fixtures
# ---------------------------------------------------------------------------

BILL_UNPAID = {
    "Id": "123",
    "Balance": 1500.00,
    "TotalAmt": 1500.00,
    "TxnDate": "2026-03-01",
    "DueDate": "2026-04-01",
    "VendorRef": {"value": "55", "name": "Acme Corp"},
    "APAccountRef": {"value": "33", "name": "Accounts Payable"},
    "CurrencyRef": {"value": "USD"},
}

BILL_PAID = {**BILL_UNPAID, "Id": "124", "Balance": 0.00}

BILL_PARTIAL = {**BILL_UNPAID, "Id": "125", "Balance": 500.00, "TotalAmt": 1500.00}

# ---------------------------------------------------------------------------
# Account fixtures
# ---------------------------------------------------------------------------

BANK_ACCOUNT_SUFFICIENT = {
    "Id": "456",
    "Name": "Business Checking",
    "AccountType": "Bank",
    "AccountSubType": "Checking",
    "CurrentBalance": 42000.00,
    "Active": True,
}

BANK_ACCOUNT_INSUFFICIENT = {
    **BANK_ACCOUNT_SUFFICIENT,
    "Id": "457",
    "Name": "Low Balance Checking",
    "CurrentBalance": 200.00,
}

EXPENSE_ACCOUNT = {
    "Id": "789",
    "Name": "Office Supplies",
    "AccountType": "Expense",
    "CurrentBalance": 0,
    "Active": True,
}

# ---------------------------------------------------------------------------
# Payment token payload fixture
# ---------------------------------------------------------------------------

SAMPLE_PAYMENT_PAYLOAD = {
    "bill_id": "123",
    "vendor_id": "55",
    "vendor_name": "Acme Corp",
    "payment_amount": 1500.00,
    "payment_account_id": "456",
    "payment_account_name": "Business Checking",
    "payment_date": "2026-04-12",
    "memo": "",
    "balance_after_payment": 0.00,
    "payment_account_balance": 42000.00,
}

# ---------------------------------------------------------------------------
# QBO API response fixtures
# ---------------------------------------------------------------------------

QBO_GET_BILL_RESPONSE = {"Bill": BILL_UNPAID, "time": "2026-04-12T14:00:00Z"}

QBO_BILL_NOT_FOUND_RESPONSE = {
    "Fault": {
        "Error": [{"Message": "Object Not Found", "code": "610"}],
        "type": "SERVICE",
    }
}

QBO_CREATE_BILL_PAYMENT_RESPONSE = {
    "BillPayment": {
        "Id": "789",
        "PayType": "Check",
        "TotalAmt": 1500.00,
        "TxnDate": "2026-04-12",
        "VendorRef": {"value": "55", "name": "Acme Corp"},
        "CheckPayment": {"BankAccountRef": {"value": "456", "name": "Business Checking"}},
        "MetaData": {"CreateTime": "2026-04-12T14:32:00Z"},
    }
}

QBO_BILL_PAYMENTS_EMPTY = {"QueryResponse": {"BillPayment": [], "totalCount": 0}}

QBO_BILL_PAYMENTS_RECENT = {
    "QueryResponse": {
        "totalCount": 1,
        "BillPayment": [
            {
                "Id": "700",
                "TxnDate": "2026-04-12",
                "TotalAmt": 1500.00,
                "Line": [{"LinkedTxn": [{"TxnId": "123", "TxnType": "Bill"}]}],
                "MetaData": {"CreateTime": "2026-04-12T10:00:00Z"},
            }
        ],
    }
}

# ---------------------------------------------------------------------------
# Autouse fixture: clear the token store between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_token_store():
    """Clear the in-memory payment token store before and after every test.

    This prevents token state generated in one test from leaking into the next.
    The try/except handles the case where payment_tokens has not been imported
    yet (e.g., early in a test session before the module is touched).
    """
    try:
        from payment_tokens import clear_store
        clear_store()
    except ImportError:
        pass
    yield
    try:
        from payment_tokens import clear_store
        clear_store()
    except ImportError:
        pass
