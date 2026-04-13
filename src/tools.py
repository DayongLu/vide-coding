"""
Shared Anthropic tool definitions for the Finance Agent.

This is the single source of truth for all 13 QBO tools exposed to Claude.
Both app.py and chat.py import TOOLS from here. The MCP server
(qbo_mcp_server.py) derives its tool set from the same qbo_client functions,
so any addition there must be mirrored here.

Tool count: 10 read-only + 3 bill payment write tools = 13 total.
"""

from typing import Any

TOOLS: list[dict[str, Any]] = [
    # ------------------------------------------------------------------
    # Read-only tools
    # ------------------------------------------------------------------
    {
        "name": "get_company_info",
        "description": "Get company information from QuickBooks (name, address, email, etc.)",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_vendors",
        "description": "Get list of vendors/suppliers from QuickBooks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of vendors to return. Default 100.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_bills",
        "description": "Get all bills (invoices received from vendors / accounts payable) from QuickBooks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of bills to return. Default 100.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_unpaid_bills",
        "description": "Get only unpaid bills (bills with a remaining balance > 0) from QuickBooks.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_bill_payments",
        "description": "Get bill payment records from QuickBooks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of bill payments to return. Default 50.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_accounts",
        "description": (
            "Get chart of accounts from QuickBooks. Can filter by account type "
            "(e.g., 'Expense', 'Income', 'Bank', 'Accounts Payable', 'Accounts Receivable')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account_type": {
                    "type": "string",
                    "description": "Filter by account type. Examples: 'Expense', 'Income', 'Bank', 'Accounts Payable'.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_invoices",
        "description": "Get invoices (bills sent TO customers / accounts receivable) from QuickBooks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of invoices to return. Default 50.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_customers",
        "description": "Get list of customers from QuickBooks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of customers to return. Default 100.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_profit_and_loss",
        "description": "Get the Profit and Loss (Income Statement) report from QuickBooks.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_balance_sheet",
        "description": "Get the Balance Sheet report from QuickBooks.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    # ------------------------------------------------------------------
    # Write tools — bill payment flow (two-step: preview then execute)
    # ------------------------------------------------------------------
    {
        "name": "get_bill_by_id",
        "description": "Fetch a single bill by its QuickBooks ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "bill_id": {
                    "type": "string",
                    "description": "Numeric QuickBooks bill ID (e.g. '123').",
                }
            },
            "required": ["bill_id"],
        },
    },
    {
        "name": "preview_bill_payment",
        "description": (
            "Validate a proposed bill payment and return a preview with a confirmation token. "
            "This is STEP 1 of the two-step payment flow. Present the preview to the user "
            "and ask for explicit confirmation before calling create_bill_payment."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "bill_id": {
                    "type": "string",
                    "description": "Numeric QBO bill ID.",
                },
                "payment_account_id": {
                    "type": "string",
                    "description": "Numeric QBO Bank account ID to pay from.",
                },
                "amount": {
                    "type": "number",
                    "description": "Payment amount. Defaults to the full bill balance if omitted.",
                },
                "payment_date": {
                    "type": "string",
                    "description": "ISO 8601 date (YYYY-MM-DD). Defaults to today if omitted.",
                },
                "memo": {
                    "type": "string",
                    "description": "Optional private note (max 4000 chars).",
                },
            },
            "required": ["bill_id", "payment_account_id"],
        },
    },
    {
        "name": "create_bill_payment",
        "description": (
            "Execute a bill payment in QuickBooks using a previously generated confirmation token. "
            "This is STEP 2 of the two-step payment flow. Only call this after the user has "
            "explicitly reviewed the preview and confirmed the payment. "
            "Never pass user_confirmed=true unless the user has genuinely confirmed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "confirmation_token": {
                    "type": "string",
                    "description": "Token returned by preview_bill_payment. Expires after 5 minutes.",
                },
                "user_confirmed": {
                    "type": "boolean",
                    "description": "Must be true to proceed. Pass false to abort without consuming the token.",
                },
            },
            "required": ["confirmation_token", "user_confirmed"],
        },
    },
]

# Convenience set for parity checks (e.g. tests, MCP server sync assertions).
TOOL_NAMES: frozenset[str] = frozenset(t["name"] for t in TOOLS)


def execute_tool(tool_name: str, tool_input: dict) -> str:
    """Dispatch a tool call to the appropriate qbo_client function.

    This is the synchronous dispatcher used by app.py, chat.py, and the
    async API layer (wrapped in asyncio.to_thread). Returns a JSON string
    in all cases — errors are serialised as {"error": "..."} rather than
    raised so that the agent loop can continue.

    Args:
        tool_name: Name of the tool to execute.
        tool_input: Tool input parameters from the Anthropic API.

    Returns:
        JSON string result.
    """
    import json
    import qbo_client

    try:
        if tool_name == "get_company_info":
            result = qbo_client.get_company_info()
        elif tool_name == "get_vendors":
            result = qbo_client.get_vendors(tool_input.get("max_results", 100))
        elif tool_name == "get_bills":
            result = qbo_client.get_bills(tool_input.get("max_results", 100))
        elif tool_name == "get_unpaid_bills":
            result = qbo_client.get_unpaid_bills()
        elif tool_name == "get_bill_payments":
            result = qbo_client.get_bill_payments(tool_input.get("max_results", 50))
        elif tool_name == "get_accounts":
            result = qbo_client.get_accounts(tool_input.get("account_type"))
        elif tool_name == "get_invoices":
            result = qbo_client.get_invoices(tool_input.get("max_results", 50))
        elif tool_name == "get_customers":
            result = qbo_client.get_customers(tool_input.get("max_results", 100))
        elif tool_name == "get_profit_and_loss":
            result = qbo_client.get_profit_and_loss()
        elif tool_name == "get_balance_sheet":
            result = qbo_client.get_balance_sheet()
        elif tool_name == "get_bill_by_id":
            result = qbo_client.get_bill_by_id(tool_input["bill_id"])
        elif tool_name == "preview_bill_payment":
            result = qbo_client.preview_bill_payment(
                bill_id=tool_input["bill_id"],
                payment_account_id=tool_input["payment_account_id"],
                amount=tool_input.get("amount"),
                payment_date=tool_input.get("payment_date"),
                memo=tool_input.get("memo", ""),
            )
        elif tool_name == "create_bill_payment":
            import payment_tokens
            token = tool_input["confirmation_token"]
            user_confirmed = tool_input["user_confirmed"]
            if not user_confirmed:
                return json.dumps({"error": "Payment not confirmed by user."})
            payload = payment_tokens.consume_token(token)
            result = qbo_client.create_bill_payment(payload)
        else:
            result = {"error": f"Unknown tool: {tool_name}"}
        return json.dumps(result, indent=2, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
