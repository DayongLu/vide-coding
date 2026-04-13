"""
Abstract base class for LLM providers in the Finance Agent API.

Normalizes the streaming interface across Anthropic, Gemini, and OpenAI
so the core agent loop can remain provider-agnostic.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any


class BaseLLMProvider(ABC):
    """Base interface for all LLM backends."""

    @abstractmethod
    async def stream_turn(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system_prompt: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Run a single LLM turn and yield normalized events.

        Args:
            messages: Conversation history in a standardized format.
            tools: Tool definitions (Anthropic-style JSON schema).
            system_prompt: System instructions for this turn.

        Yields:
            Dicts with event types: 'token', 'tool_start', 'tool_end',
            'done', or 'error'.
        """
        pass
