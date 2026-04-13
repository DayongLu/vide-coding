"""
Finance Agent POC - Web-based chat interface.
"""

import json
import os

import anthropic
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify

import qbo_client

load_dotenv()

app = Flask(__name__)

TOOLS = [
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
        "description": "Get chart of accounts from QuickBooks. Can filter by account type (e.g., 'Expense', 'Income', 'Bank', 'Accounts Payable', 'Accounts Receivable').",
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
]

SYSTEM_PROMPT = """You are an accounts payable assistant for a small business. You help the user understand their QuickBooks data by answering questions in plain language.

You have access to tools that query QuickBooks Online. Use them to answer the user's questions accurately.

When presenting financial data:
- Format dollar amounts clearly (e.g., $1,234.56)
- Summarize when there's a lot of data — don't dump raw JSON
- Highlight what's important (overdue bills, large amounts, etc.)
- Be concise but thorough
- Use markdown formatting for readability

You HELP the user by:
- Answering questions about their vendors, bills, payments, and accounts
- Flagging anomalies (duplicates, unusual amounts, overdue items)
- Summarizing financial status

You DO NOT:
- Give tax advice
- Recommend financial strategies
- Make payment decisions on behalf of the user

Today's date is 2026-04-12.

When the user asks something beyond your scope, provide the factual information and suggest they consult their accountant."""


def execute_tool(tool_name, tool_input):
    """Execute a tool call and return the result."""
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
        else:
            result = {"error": f"Unknown tool: {tool_name}"}
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


# Store conversation per session (simple in-memory for POC)
conversations = {}

client = anthropic.Anthropic()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    user_message = data.get("message", "").strip()
    session_id = data.get("session_id", "default")

    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    # Get or create conversation history
    if session_id not in conversations:
        conversations[session_id] = []

    messages = conversations[session_id]
    messages.append({"role": "user", "content": user_message})

    # Track tool calls for display
    tool_calls_made = []

    # Call Claude with tools
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        tools=TOOLS,
        messages=messages,
    )

    # Handle tool use loops
    while response.stop_reason == "tool_use":
        assistant_content = response.content
        messages.append({"role": "assistant", "content": assistant_content})

        tool_results = []
        for block in assistant_content:
            if block.type == "tool_use":
                tool_calls_made.append(block.name)
                result = execute_tool(block.name, block.input)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    }
                )

        messages.append({"role": "user", "content": tool_results})

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

    # Extract text response
    assistant_text = ""
    for block in response.content:
        if hasattr(block, "text"):
            assistant_text += block.text

    messages.append({"role": "assistant", "content": response.content})

    return jsonify({
        "response": assistant_text,
        "tool_calls": tool_calls_made,
    })


@app.route("/reset", methods=["POST"])
def reset():
    data = request.json
    session_id = data.get("session_id", "default")
    conversations.pop(session_id, None)
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=True, port=5001)
