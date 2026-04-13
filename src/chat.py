"""
Finance Agent POC - Chat interface that answers questions using QuickBooks data.
"""

import json
import os
import sys

import anthropic
from dotenv import load_dotenv

sys.path.append(os.path.dirname(__file__))

import qbo_client
from api.system_prompt import build_system_prompt
from tools import TOOLS, execute_tool

load_dotenv()


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
            system=build_system_prompt(),
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
                system=build_system_prompt(),
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
