"""
OpenAI-compatible implementation of the LLM provider.
Can be used with OpenAI, Ollama, vLLM, etc.
"""

import json
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

from openai import AsyncOpenAI

from api.providers.base import BaseLLMProvider

logger = logging.getLogger(__name__)


class OpenAIProvider(BaseLLMProvider):
    """Provider for OpenAI-compatible APIs (OpenAI, Ollama, vLLM)."""

    def __init__(
        self, 
        model: str | None = None, 
        base_url: str | None = None, 
        api_key: str | None = None
    ):
        """Initialize the OpenAI client.

        Args:
            model: Model identifier. Defaults to OPENAI_MODEL env var or 'gpt-4o'.
            base_url: API base URL. Defaults to OPENAI_BASE_URL or OpenAI default.
            api_key: API key. Defaults to OPENAI_API_KEY.
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "ollama") # Ollama doesn't need a real key
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o")
        
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )

    async def stream_turn(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system_prompt: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Run a single OpenAI-compatible turn and yield normalized events."""
        try:
            # Convert tools to OpenAI format
            openai_tools = []
            for t in tools:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t.get("input_schema", {})
                    }
                })

            # Add system prompt as the first message if not empty
            full_messages = []
            if system_prompt:
                full_messages.append({"role": "system", "content": system_prompt})
            
            # OpenAI uses 'assistant' for model messages, same as Anthropic.
            # But we might need to normalize 'tool_use' and 'tool_result' blocks
            # for the OpenAI API if they are in the history.
            normalized_messages = self._normalize_history(messages)
            full_messages.extend(normalized_messages)

            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                tools=openai_tools if openai_tools else None,
                stream=True,
            )

            current_tool_calls = []
            full_text = ""

            async for chunk in stream:
                if not chunk.choices:
                    continue
                
                delta = chunk.choices[0].delta
                
                # Handle text tokens
                if delta.content:
                    full_text += delta.content
                    yield {"type": "token", "text": delta.content}
                
                # Handle tool calls
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        if tc.index is not None:
                            # If it's a new call starting
                            if len(current_tool_calls) <= tc.index:
                                current_tool_calls.append({
                                    "id": tc.id,
                                    "name": tc.function.name,
                                    "input_raw": ""
                                })
                                if tc.function.name:
                                    yield {"type": "tool_start", "tool": tc.function.name}
                            
                            # Accumulate arguments
                            if tc.function.arguments:
                                current_tool_calls[tc.index]["input_raw"] += tc.function.arguments

            # Finalize tool calls
            final_tool_calls = []
            for tc in current_tool_calls:
                try:
                    args = json.loads(tc["input_raw"]) if tc["input_raw"] else {}
                    final_tool_calls.append({
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": args
                    })
                except json.JSONDecodeError:
                    logger.error("Failed to parse tool arguments: %s", tc["input_raw"])
                    final_tool_calls.append({
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": {}
                    })

            # Format the final assistant message for the 'done' event
            assistant_content = []
            if full_text:
                assistant_content.append({"type": "text", "text": full_text})
            
            for tc in final_tool_calls:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["input"]
                })

            yield {
                "type": "done",
                "content": assistant_content,
                "stop_reason": "tool_use" if final_tool_calls else "stop",
                "tool_calls": final_tool_calls
            }

        except Exception as exc:
            logger.exception("OpenAI provider error")
            yield {
                "type": "error",
                "error_code": "UPSTREAM_AI_ERROR",
                "message": str(exc),
                "recoverable": True,
            }

    def _normalize_history(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize history from Anthropic/Internal format to OpenAI format."""
        openai_msgs = []
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            
            if role == "user":
                if isinstance(content, list):
                    # Handle tool_result blocks
                    parts = []
                    for block in content:
                        if block.get("type") == "tool_result":
                            # In OpenAI, tool results are separate messages with role 'tool'
                            # This is tricky because we are inside a loop mapping 1:1.
                            # For now, let's just extract text if any.
                            pass
                        elif block.get("type") == "text":
                            parts.append(block["text"])
                    openai_msgs.append({"role": "user", "content": " ".join(parts) if parts else str(content)})
                else:
                    openai_msgs.append({"role": "user", "content": content})
            
            elif role == "assistant":
                # OpenAI assistant messages can have tool_calls
                msg_obj = {"role": "assistant"}
                if isinstance(content, list):
                    text = "".join(b["text"] for b in content if b.get("type") == "text")
                    if text:
                        msg_obj["content"] = text
                    
                    tool_calls = []
                    for b in content:
                        if b.get("type") == "tool_use":
                            tool_calls.append({
                                "id": b["id"],
                                "type": "function",
                                "function": {
                                    "name": b["name"],
                                    "arguments": json.dumps(b["input"])
                                }
                            })
                    if tool_calls:
                        msg_obj["tool_calls"] = tool_calls
                else:
                    msg_obj["content"] = content
                openai_msgs.append(msg_obj)
            
            elif role == "tool_result":
                # Handle tool result from previous turn
                if isinstance(content, list):
                    for block in content:
                         openai_msgs.append({
                            "role": "tool",
                            "tool_call_id": block["tool_use_id"],
                            "content": block["content"]
                        })
                else:
                    # Should not happen with our internal persistence
                    pass
            
            elif role == "tool_use":
                # Already handled in the assistant block above if they were grouped
                pass
                
        return openai_msgs
