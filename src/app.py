"""
Finance Agent POC - Web-based chat interface.
"""

import json
import os
import sys

# Add src to path so we can import from api.agent
sys.path.append(os.path.dirname(__file__))

from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify

import qbo_client
from tools import TOOLS, execute_tool
from api.agent import get_provider
from api.system_prompt import build_system_prompt

load_dotenv()

app = Flask(__name__)

# Store conversation per session (simple in-memory for POC)
conversations = {}

@app.route("/")
def index():
    return render_template("index.html")

import asyncio

@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    return asyncio.run(_chat_impl(data))

async def _chat_impl(data):
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
    
    provider = get_provider()
    system_prompt = build_system_prompt()
    
    # Simple non-streaming wrapper for the legacy UI
    full_text = ""
    assistant_content = []
    
    while True:
        # Run one turn
        gen = provider.stream_turn(messages, TOOLS, system_prompt)
        async for event in gen:
            if event["type"] == "token":
                full_text += event["text"]
            elif event["type"] == "done":
                assistant_content = event["content"]
                tool_calls = event["tool_calls"]
        
        messages.append({"role": "assistant", "content": assistant_content})
        
        if not tool_calls:
            break
            
        # Execute tools
        tool_results = []
        for tc in tool_calls:
            tool_calls_made.append(tc["name"])
            result = execute_tool(tc["name"], tc["input"])
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc["id"],
                "content": result,
            })
        
        messages.append({"role": "user", "content": tool_results})

    return jsonify({
        "response": full_text,
        "tool_calls": tool_calls_made,
    })


@app.route("/reset", methods=["POST"])
def reset():
    data = request.json
    session_id = data.get("session_id", "default")
    conversations.pop(session_id, None)
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    app.run(debug=True, host=host, port=5001)
