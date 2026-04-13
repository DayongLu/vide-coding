"""
QuickBooks Online API client - handles all QBO queries.
"""

import json
import os

import requests
from dotenv import load_dotenv

load_dotenv()

ENVIRONMENT = os.getenv("QBO_ENVIRONMENT", "sandbox")
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "tokens.json")

if ENVIRONMENT == "sandbox":
    BASE_URL = "https://sandbox-quickbooks.api.intuit.com"
else:
    BASE_URL = "https://quickbooks.api.intuit.com"


def load_tokens():
    with open(TOKEN_FILE) as f:
        return json.load(f)


def qbo_request(endpoint, tokens=None):
    """Make a GET request to QBO API."""
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


def query(sql, tokens=None):
    """Run a QBO query (SQL-like syntax)."""
    return qbo_request(f"query?query={sql}", tokens)


def get_company_info(tokens=None):
    if tokens is None:
        tokens = load_tokens()
    realm_id = tokens["realm_id"]
    data = qbo_request(f"companyinfo/{realm_id}", tokens)
    return data["CompanyInfo"]


def get_vendors(max_results=100, tokens=None):
    data = query(f"SELECT * FROM Vendor MAXRESULTS {max_results}", tokens)
    return data.get("QueryResponse", {}).get("Vendor", [])


def get_bills(max_results=100, tokens=None):
    data = query(f"SELECT * FROM Bill MAXRESULTS {max_results}", tokens)
    return data.get("QueryResponse", {}).get("Bill", [])


def get_unpaid_bills(tokens=None):
    data = query("SELECT * FROM Bill WHERE Balance > '0' MAXRESULTS 100", tokens)
    return data.get("QueryResponse", {}).get("Bill", [])


def get_bill_payments(max_results=50, tokens=None):
    data = query(f"SELECT * FROM BillPayment MAXRESULTS {max_results}", tokens)
    return data.get("QueryResponse", {}).get("BillPayment", [])


def get_accounts(account_type=None, max_results=100, tokens=None):
    if account_type:
        sql = f"SELECT * FROM Account WHERE AccountType = '{account_type}' MAXRESULTS {max_results}"
    else:
        sql = f"SELECT * FROM Account MAXRESULTS {max_results}"
    data = query(sql, tokens)
    return data.get("QueryResponse", {}).get("Account", [])


def get_invoices(max_results=50, tokens=None):
    data = query(f"SELECT * FROM Invoice MAXRESULTS {max_results}", tokens)
    return data.get("QueryResponse", {}).get("Invoice", [])


def get_customers(max_results=100, tokens=None):
    data = query(f"SELECT * FROM Customer MAXRESULTS {max_results}", tokens)
    return data.get("QueryResponse", {}).get("Customer", [])


def get_profit_and_loss(tokens=None):
    """Get P&L report."""
    if tokens is None:
        tokens = load_tokens()
    return qbo_request("reports/ProfitAndLoss", tokens)


def get_balance_sheet(tokens=None):
    """Get Balance Sheet report."""
    if tokens is None:
        tokens = load_tokens()
    return qbo_request("reports/BalanceSheet", tokens)
