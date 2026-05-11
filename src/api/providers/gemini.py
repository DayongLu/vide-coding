"""
Google Gemini implementation of the LLM provider.
"""

import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

from google import genai
from google.genai import types

from api.providers.base import BaseLLMProvider

logger = logging.getLogger(__name__)


def _anthropic_to_gemini_messages(messages: list[dict[str, Any]]) -> list[types.Content]:
    """Convert Anthropic-style message history to Gemini Content objects.
    
    Anthropic uses {'role': 'user'|'assistant', 'content': str|list}.
    Gemini uses parts with text or function_call/function_response.
    """
    gemini_history = []
    
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        content = msg["content"]
        
        parts = []
        if isinstance(content, str):
            parts.append(types.Part.from_text(text=content))
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(types.Part.from_text(text=block["text"]))
                    elif block.get("type") == "tool_use":
                        parts.append(types.Part.from_function_call(
                            name=block["name"],
                            args=block["input"]
                        ))
                    elif block.get("type") == "tool_result":
                        # content in tool_result is the JSON string from the tool
                        try:
                            resp_json = block.get("content", "{}")
                            # If it's already a string, we might need to parse it if Gemini expects a dict
                            # but let's try passing the string for now. 
                            # Actually, Gemini FunctionResponse 'response' field is a dict.
                            import json
                            resp_data = json.loads(resp_json) if isinstance(resp_json, str) else resp_json
                            parts.append(types.Part.from_function_response(
                                name=block.get("name", "unknown"), # We might need to track names better
                                response={"result": resp_data}
                            ))
                        except Exception:
                            parts.append(types.Part.from_function_response(
                                name="error",
                                response={"error": "failed to parse tool result"}
                            ))
        
        if parts:
            gemini_history.append(types.Content(role=role, parts=parts))
            
    return gemini_history


def _anthropic_to_gemini_tools(tools: list[dict[str, Any]]) -> list[types.Tool]:
    """Convert Anthropic tool definitions to Gemini Tool objects."""
    declarations = []
    for t in tools:
        # Gemini expects properties, required, etc. directly in parameters.
        # Anthropic has them inside 'input_schema'.
        schema = t.get("input_schema", {})
        
        # FunctionDeclaration parameters must be a dict representing the JSON schema
        declarations.append(types.FunctionDeclaration(
            name=t["name"],
            description=t["description"],
            parameters=schema
        ))
    
    return [types.Tool(function_declarations=declarations)]


class GeminiProvider(BaseLLMProvider):
    """Provider for Google's Gemini models."""

    def __init__(self, api_key: str | None = None, model: str = "gemini-2.0-flash"):
        """Initialize the Gemini client.

        Args:
            api_key: Google Gemini API key. Defaults to GEMINI_API_KEY env var.
            model: Gemini model identifier string.
        """
        api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.error("GEMINI_API_KEY not set")
        
        self.client = genai.Client(api_key=api_key, http_options={'api_version': 'v1alpha'})
        self.model = model

    async def stream_turn(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system_prompt: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Run a single Gemini turn and yield normalized events."""
        try:
            gemini_history = _anthropic_to_gemini_messages(messages)
            gemini_tools = _anthropic_to_gemini_tools(tools)
            
            # Gemini system instruction is separate from history
            config = types.GenerateContentConfig(
                system_instruction=system_prompt,
                tools=gemini_tools,
            )

            # Note: The google-genai SDK 1.x might have different async patterns.
            # Assuming standard async iterator for stream.
            async for chunk in await self.client.aio.models.generate_content_stream(
                model=self.model,
                contents=gemini_history,
                config=config,
            ):
                # Process text parts
                for part in chunk.candidates[0].content.parts:
                    if part.text:
                        yield {"type": "token", "text": part.text}
                    
                    if part.function_call:
                        # Gemini emits the whole call at once (usually)
                        call = part.function_call
                        yield {"type": "tool_start", "tool": call.name}
                
            # Final state
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=gemini_history,
                config=config,
            )
            
            # Extract final message and tool calls for the 'done' event
            candidate = response.candidates[0]
            assistant_parts = []
            tool_calls = []
            
            for part in candidate.content.parts:
                if part.text:
                    assistant_parts.append({"type": "text", "text": part.text})
                if part.function_call:
                    call = part.function_call
                    # Convert to Anthropic-style tool_use block
                    tool_calls.append({
                        "id": f"call_{call.name}", # Gemini doesn't always provide a call ID in the same way
                        "name": call.name,
                        "input": call.args
                    })
                    assistant_parts.append({
                        "type": "tool_use",
                        "id": f"call_{call.name}",
                        "name": call.name,
                        "input": call.args
                    })

            yield {
                "type": "done",
                "content": assistant_parts,
                "stop_reason": "tool_use" if tool_calls else "end_turn",
                "tool_calls": tool_calls
            }

        except Exception as exc:
            logger.exception("Gemini provider error")
            yield {
                "type": "error",
                "error_code": "UPSTREAM_AI_ERROR",
                "message": str(exc),
                "recoverable": True,
            }
