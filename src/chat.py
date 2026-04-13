"""
Finance Agent POC - Chat interface that answers questions using QuickBooks data.
"""

import json
import os

import anthropic
from dotenv import load_dotenv

import qbo_client
from tools import TOOLS

load_dotenv()

SYSTEM_PROMPT = """You are an accounts payable assistant for a small business. You help the user understand their QuickBooks data by answering questions in plain language.

You have access to tools that query QuickBooks Online. Use them to answer the user's questions accurately.

When presenting financial data:
- Format dollar amounts clearly (e.g., $1,234.56)
- Summarize when there's a lot of data — don't dump raw JSON
- Highlight what's important (overdue bills, large amounts, etc.)
- Be concise but thorough

You HELP the user by:
- Answering questions about their vendors, bills, payments, and accounts
- Flagging anomalies (duplicates, unusual amounts, overdue items)
- Summarizing financial status

You DO NOT:
- Give tax advice
- Recommend financial strategies
- Make payment decisions on behalf of the user

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


def chat():
    """Main chat loop."""
    client = anthropic.Anthropic()
    messages = []

    print("=" * 60)
    print("  Finance Agent POC")
    print("  Ask me anything about your QuickBooks data!")
    print("  Type 'quit' to exit.")
    print("=" * 60)
    print()

    while True:
        user_input = input("You: ").strip()
        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            break

        messages.append({"role": "user", "content": user_input})

        # Call Claude with tools
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # Process response - handle tool use loops
        while response.stop_reason == "tool_use":
            # Collect assistant message
            assistant_content = response.content
            messages.append({"role": "assistant", "content": assistant_content})

            # Execute all tool calls
            tool_results = []
            for block in assistant_content:
                if block.type == "tool_use":
                    print(f"  [Querying QuickBooks: {block.name}...]")
                    result = execute_tool(block.name, block.input)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        }
                    )

            messages.append({"role": "user", "content": tool_results})

            # Get next response
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )

        # Extract and print text response
        assistant_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                assistant_text += block.text

        messages.append({"role": "assistant", "content": response.content})
        print(f"\nAgent: {assistant_text}\n")


if __name__ == "__main__":
    chat()
