"""
Finance Agent POC - Web-based chat interface.
"""

import json
import os

import anthropic
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify

import qbo_client
from tools import TOOLS, execute_tool

load_dotenv()

app = Flask(__name__)

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
