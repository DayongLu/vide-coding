"""
Anthropic Claude implementation of the LLM provider.
"""

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

import anthropic

from api.providers.base import BaseLLMProvider

logger = logging.getLogger(__name__)


class AnthropicProvider(BaseLLMProvider):
    """Provider for Anthropic's Claude family of models."""

    def __init__(self, model: str = "claude-sonnet-4-20250514"):
        """Initialize the Anthropic client.

        Args:
            model: Claude model identifier string.
        """
        self.client = anthropic.AsyncAnthropic()
        self.model = model

    async def stream_turn(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system_prompt: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Run a single Claude turn and yield normalized events."""
        try:
            async with self.client.messages.stream(
                model=self.model,
                max_tokens=4096,
                system=system_prompt,
                tools=tools,
                messages=messages,
            ) as stream:
                tool_input_buffer: dict[str, str] = {}  # tool_use id → partial JSON
                current_tool_uses: list[dict] = []

                async for event in stream:
                    event_type = type(event).__name__
                    logger.debug("anthropic_event type=%s", event_type)

                    if event_type == "RawContentBlockStartEvent":
                        block = event.content_block
                        if block.type == "tool_use":
                            tool_input_buffer[block.id] = ""
                            current_tool_uses.append(
                                {"id": block.id, "name": block.name, "input": {}}
                            )
                            logger.info("tool_detected name=%s id=%s", block.name, block.id)
                            yield {"type": "tool_start", "tool": block.name}

                    elif event_type == "RawContentBlockDeltaEvent":
                        delta = event.delta
                        if delta.type == "text_delta":
                            yield {"type": "token", "text": delta.text}
                        elif delta.type == "input_json_delta":
                            if current_tool_uses:
                                last_id = current_tool_uses[-1]["id"]
                                tool_input_buffer[last_id] = (
                                    tool_input_buffer.get(last_id, "")
                                    + delta.partial_json
                                )
                    
                    elif event_type == "InputJsonEvent":
                         if current_tool_uses:
                                last_id = current_tool_uses[-1]["id"]
                                tool_input_buffer[last_id] = (
                                    tool_input_buffer.get(last_id, "")
                                    + event.partial_json
                                )

                    elif event_type == "RawContentBlockStopEvent":
                         # Process parsed input if a tool use block ended
                         if current_tool_uses:
                            last_tu = current_tool_uses[-1]
                            if last_tu["input"] == {}:
                                raw = tool_input_buffer.get(last_tu["id"], "{}")
                                try:
                                    last_tu["input"] = json.loads(raw) if raw else {}
                                    logger.info("tool_input_parsed name=%s input=%s", last_tu["name"], last_tu["input"])
                                except json.JSONDecodeError:
                                    logger.error("tool_input_invalid_json name=%s raw=%s", last_tu["name"], raw)
                                    last_tu["input"] = {}

                # Final state after stream ends
                final_msg = await stream.get_final_message()
                assistant_content = [
                    block.model_dump() if hasattr(block, "model_dump") else block
                    for block in final_msg.content
                ]
                stop_reason = final_msg.stop_reason
                
                # Re-map stop_reason to generic provider-agnostic reasons if needed
                yield {
                    "type": "done",
                    "content": assistant_content,
                    "stop_reason": stop_reason,
                    "tool_calls": current_tool_uses if stop_reason == "tool_use" else []
                }

        except Exception as exc:
            logger.exception("Anthropic provider error")
            yield {
                "type": "error",
                "error_code": "UPSTREAM_AI_ERROR",
                "message": str(exc),
                "recoverable": True,
            }
