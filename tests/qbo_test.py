"""
Quick test: connect to QBO sandbox and pull company info + some data.
Run qbo_auth.py first to get tokens.
"""

import json
import os
import sys

import requests
from dotenv import load_dotenv

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
load_dotenv(os.path.join(SRC_DIR, ".env"))

ENVIRONMENT = os.getenv("QBO_ENVIRONMENT", "sandbox")

if ENVIRONMENT == "sandbox":
    BASE_URL = "https://sandbox-quickbooks.api.intuit.com"
else:
    BASE_URL = "https://quickbooks.api.intuit.com"

TOKEN_FILE = os.path.join(SRC_DIR, "tokens.json")


def load_tokens():
    if not os.path.exists(TOKEN_FILE):
        print("No tokens found. Run qbo_auth.py first.")
        sys.exit(1)
    with open(TOKEN_FILE) as f:
        return json.load(f)


def qbo_get(endpoint, tokens):
    """Make a GET request to the QBO API."""
    realm_id = tokens["realm_id"]
    url = f"{BASE_URL}/v3/company/{realm_id}/{endpoint}"
    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {tokens['access_token']}",
            "Accept": "application/json",
        },
    )
    if response.status_code == 401:
        print("Token expired. Need to refresh or re-authorize.")
        sys.exit(1)
    response.raise_for_status()
    return response.json()


def main():
    tokens = load_tokens()
    realm_id = tokens.get("realm_id")
    print(f"Connected to QBO sandbox (Realm ID: {realm_id})")
    print("=" * 60)

    # 1. Company Info
    print("\n--- Company Info ---")
    data = qbo_get("companyinfo/" + realm_id, tokens)
    info = data["CompanyInfo"]
    print(f"  Company Name: {info['CompanyName']}")
    print(f"  Country:      {info.get('Country', 'N/A')}")
    print(f"  Email:        {info.get('Email', {}).get('Address', 'N/A')}")

    # 2. Vendors
    print("\n--- Vendors ---")
    data = qbo_get("query?query=SELECT * FROM Vendor MAXRESULTS 10", tokens)
    vendors = data.get("QueryResponse", {}).get("Vendor", [])
    if vendors:
        for v in vendors:
            print(f"  - {v['DisplayName']} (ID: {v['Id']})")
    else:
        print("  No vendors found.")

    # 3. Bills (Accounts Payable)
    print("\n--- Bills (Payables) ---")
    data = qbo_get("query?query=SELECT * FROM Bill MAXRESULTS 10", tokens)
    bills = data.get("QueryResponse", {}).get("Bill", [])
    if bills:
        for b in bills:
            vendor_name = b.get("VendorRef", {}).get("name", "Unknown")
            print(f"  - Bill #{b.get('DocNumber', 'N/A')} | "
                  f"Vendor: {vendor_name} | "
                  f"Amount: ${b['TotalAmt']:.2f} | "
                  f"Due: {b.get('DueDate', 'N/A')}")
    else:
        print("  No bills found.")

    # 4. Accounts (Chart of Accounts)
    print("\n--- Chart of Accounts (Expense accounts) ---")
    data = qbo_get(
        "query?query=SELECT * FROM Account WHERE AccountType = 'Expense' MAXRESULTS 10",
        tokens,
    )
    accounts = data.get("QueryResponse", {}).get("Account", [])
    if accounts:
        for a in accounts:
            print(f"  - {a['Name']} (ID: {a['Id']})")
    else:
        print("  No expense accounts found.")

    print("\n" + "=" * 60)
    print("Connection test complete!")


if __name__ == "__main__":
    main()
