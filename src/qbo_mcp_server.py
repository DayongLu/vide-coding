"""
QuickBooks Online MCP Server.

Exposes QBO data as MCP tools so any LLM client (Claude Code, Claude Desktop,
Cursor, etc.) can query QuickBooks.

Usage:
    python3.13 qbo_mcp_server.py              # stdio (default, for Claude Code)
    python3.13 qbo_mcp_server.py --transport sse  # SSE on port 8080
"""

import argparse
import json

from mcp.server.fastmcp import FastMCP

import qbo_client

mcp = FastMCP(
    name="QuickBooks Online",
    instructions="Query QuickBooks Online accounting data: vendors, bills, payments, invoices, customers, accounts, and financial reports.",
    host="127.0.0.1",
    port=8080,
)


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
