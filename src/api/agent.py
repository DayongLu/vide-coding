"""
Async streaming agent loop for the Finance Agent API.

Dispatches turns to LLM providers (Anthropic, Gemini, etc.), handles tool execution,
persists messages to SQLite, and yields SSE-formatted events for the HTTP layer.
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

from api.providers.base import BaseLLMProvider
from api.providers.anthropic import AnthropicProvider
from api.providers.gemini import GeminiProvider
from api.providers.openai import OpenAIProvider
from api.system_prompt import build_system_prompt
from tools import TOOLS, execute_tool

logger = logging.getLogger(__name__)


def _now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _sse(event: str, data: dict) -> str:
    """Format a server-sent event string."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _persist_message(
    db: sqlite3.Connection,
    conv_id: str,
    role: str,
    content,
    is_internal: bool,
) -> None:
    """Insert a message row into the database."""
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


def get_provider() -> BaseLLMProvider:
    """Resolve the active LLM provider based on environment variables."""
    provider_name = os.getenv("LLM_PROVIDER", "anthropic").lower()
    
    if provider_name == "anthropic":
        return AnthropicProvider()
    elif provider_name == "gemini":
        return GeminiProvider()
    elif provider_name == "openai":
        return OpenAIProvider()
    else:
        logger.warning("Unknown provider '%s', falling back to Anthropic", provider_name)
        return AnthropicProvider()


async def run_agent_turn(
    conv_id: str,
    messages: list[dict],
    db: sqlite3.Connection,
) -> AsyncGenerator[str, None]:
    """Run one agent turn with full tool-call loop, yielding SSE events."""
    provider = get_provider()
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

            async for event in provider.stream_turn(turn_messages, TOOLS, system_prompt):
                event_type = event["type"]
                
                if event_type == "token":
                    text_buffer += event["text"]
                    full_text += event["text"]
                    yield _sse("token", {"text": event["text"]})
                
                elif event_type == "tool_start":
                    yield _sse("tool_start", {"tool": event["tool"]})
                
                elif event_type == "done":
                    assistant_content = event["content"]
                    stop_reason = event["stop_reason"]
                    current_tool_uses = event["tool_calls"]
                    logger.info("turn_complete stop_reason=%s blocks=%s", stop_reason, len(assistant_content))
                
                elif event_type == "error":
                    yield _sse("error", event)
                    return

            # Append assistant turn to in-memory history
            turn_messages.append({"role": "assistant", "content": assistant_content})

            if not current_tool_uses:
                logger.info("loop_exit tool_uses=0")
                # No more tool calls — persist final assistant text and exit loop
                _persist_message(db, conv_id, "assistant", assistant_content, is_internal=False)
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

                logger.info("tool_start name=%s conv=%s", tool_name, conv_id)
                t0 = time.monotonic()
                result_str = await asyncio.to_thread(execute_tool, tool_name, tool_input)
                duration_ms = round((time.monotonic() - t0) * 1000, 1)
                logger.info(
                    "tool_end name=%s conv=%s duration_ms=%s success=%s",
                    tool_name,
                    conv_id,
                    duration_ms,
                    not result_str.startswith('{"error"'),
                )

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
