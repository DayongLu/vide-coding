"""
Async streaming agent loop for the Finance Agent API.

Calls the Anthropic API with SSE streaming, dispatches QBO tool calls via
asyncio.to_thread(), persists messages to SQLite, and yields SSE-formatted
event strings for the HTTP layer.
"""

import asyncio
import json
import logging
import sqlite3
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

import anthropic

from api.system_prompt import build_system_prompt
from tools import TOOLS, execute_tool

logger = logging.getLogger(__name__)


def _now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _sse(event: str, data: dict) -> str:
    """Format a server-sent event string.

    Args:
        event: SSE event type.
        data: JSON-serialisable payload dict.

    Returns:
        SSE-formatted string ending with a double newline.
    """
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _persist_message(
    db: sqlite3.Connection,
    conv_id: str,
    role: str,
    content,
    is_internal: bool,
) -> None:
    """Insert a message row into the database.

    Args:
        db: Open SQLite connection.
        conv_id: Parent conversation UUID.
        role: Message role ('user', 'assistant', 'tool_use', 'tool_result').
        content: Message content (str or list of content blocks).
        is_internal: True for tool_use/tool_result rows hidden from clients.
    """
    db.execute(
        """
        INSERT INTO messages (id, conversation_id, role, content_json, timestamp, is_internal)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            conv_id,
            role,
            json.dumps(content, default=str),
            _now(),
            1 if is_internal else 0,
        ),
    )


async def run_agent_turn(
    conv_id: str,
    messages: list[dict],
    db: sqlite3.Connection,
) -> AsyncGenerator[str, None]:
    """Run one agent turn with full tool-call loop, yielding SSE events.

    Streams assistant text tokens, handles tool calls (dispatched in a
    thread pool), persists all messages, and emits a final ``done`` event.
    On any unhandled exception, emits an ``error`` SSE event instead of
    propagating to the HTTP layer.

    Args:
        conv_id: Conversation UUID for message persistence.
        messages: Full conversation history in Anthropic format.
        db: Open SQLite connection for message persistence.

    Yields:
        SSE-formatted strings (``event: ...\\ndata: ...\\n\\n``).
    """
    client = anthropic.AsyncAnthropic()
    system_prompt = build_system_prompt()
    tools_called: list[str] = []
    full_text = ""

    try:
        # Work on a mutable copy so we can append tool results across rounds.
        turn_messages = list(messages)

        while True:
            assistant_content = []
            current_tool_uses: list[dict] = []
            text_buffer = ""

            async with client.messages.stream(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                system=system_prompt,
                tools=TOOLS,
                messages=turn_messages,
            ) as stream:
                tool_input_buffer: dict[str, str] = {}  # tool_use id → partial JSON

                async for event in stream:
                    event_type = type(event).__name__

                    if event_type == "ContentBlockStartEvent":
                        block = event.content_block
                        if block.type == "tool_use":
                            tool_input_buffer[block.id] = ""
                            current_tool_uses.append(
                                {"id": block.id, "name": block.name, "input": {}}
                            )
                            yield _sse("tool_start", {"tool": block.name})

                    elif event_type == "ContentBlockDeltaEvent":
                        delta = event.delta
                        if delta.type == "text_delta":
                            text_buffer += delta.text
                            full_text += delta.text
                            yield _sse("token", {"text": delta.text})
                        elif delta.type == "input_json_delta":
                            # Find which tool_use this belongs to
                            for tu in current_tool_uses:
                                if tu["id"] == event.index or (
                                    len(current_tool_uses) == 1
                                ):
                                    # Accumulate partial JSON — index-based matching
                                    pass
                            # Accumulate in the last opened tool use
                            if current_tool_uses:
                                last_id = current_tool_uses[-1]["id"]
                                tool_input_buffer[last_id] = (
                                    tool_input_buffer.get(last_id, "")
                                    + delta.partial_json
                                )

                    elif event_type == "ContentBlockStopEvent":
                        # If we just closed a tool_use block, parse the buffered input
                        if current_tool_uses:
                            last_tu = current_tool_uses[-1]
                            if last_tu["input"] == {}:
                                raw = tool_input_buffer.get(last_tu["id"], "{}")
                                try:
                                    last_tu["input"] = json.loads(raw) if raw else {}
                                except json.JSONDecodeError:
                                    last_tu["input"] = {}

                # Collect the final message from the stream
                final_msg = await stream.get_final_message()
                assistant_content = [
                    block.model_dump() if hasattr(block, "model_dump") else block
                    for block in final_msg.content
                ]
                stop_reason = final_msg.stop_reason

            # Append assistant turn to in-memory history
            turn_messages.append({"role": "assistant", "content": assistant_content})

            if stop_reason != "tool_use" or not current_tool_uses:
                # No more tool calls — persist final assistant text and exit loop
                _persist_message(db, conv_id, "assistant", text_buffer, is_internal=False)
                db.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (_now(), conv_id),
                )
                db.commit()
                break

            # Execute tool calls
            tool_results = []
            for tu in current_tool_uses:
                tool_name = tu["name"]
                tool_input = tu["input"]
                tools_called.append(tool_name)

                logger.info("Dispatching tool: %s", tool_name)
                result_str = await asyncio.to_thread(execute_tool, tool_name, tool_input)

                summary = result_str[:100] if len(result_str) > 100 else result_str
                yield _sse("tool_end", {"tool": tool_name, "summary": summary})

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu["id"],
                        "content": result_str,
                    }
                )

                # Persist tool_use and tool_result as internal rows
                _persist_message(
                    db, conv_id, "tool_use", assistant_content, is_internal=True
                )
                _persist_message(
                    db, conv_id, "tool_result", tool_results, is_internal=True
                )

            db.commit()

            # Append tool results and continue the loop
            turn_messages.append({"role": "user", "content": tool_results})
            text_buffer = ""  # Reset for next round

        yield _sse(
            "done",
            {
                "conversation_id": conv_id,
                "tools_called": tools_called,
                "full_text": full_text,
            },
        )

    except Exception:
        logger.exception("Error in agent turn for conversation %s", conv_id)
        yield _sse(
            "error",
            {
                "error_code": "AGENT_ERROR",
                "message": "An error occurred while processing your request. Please try again.",
                "recoverable": True,
            },
        )
