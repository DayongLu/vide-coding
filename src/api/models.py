"""
Pydantic request and response models for the Finance Agent API.
"""

from typing import Any

from pydantic import BaseModel, Field


class SendMessageRequest(BaseModel):
    """Request body for POST /api/v1/conversations/{id}/messages."""

    message: str = Field(..., min_length=1, max_length=10_000)


class MessageResponse(BaseModel):
    """A single message returned to the client (internal rows excluded)."""

    id: str
    role: str
    content: str
    timestamp: str


class ConversationResponse(BaseModel):
    """Full conversation object including message history."""

    id: str
    created_at: str
    updated_at: str
    messages: list[MessageResponse]


class ConversationListItem(BaseModel):
    """Lightweight conversation summary for list responses."""

    id: str
    created_at: str
    updated_at: str
    preview: str


class ConversationListResponse(BaseModel):
    """Paginated list of conversations."""

    conversations: list[ConversationListItem]
    next_cursor: str | None = None
    total: int


class SendMessageResponse(BaseModel):
    """Non-streaming response for POST .../messages with Accept: application/json."""

    conversation_id: str
    message: MessageResponse
    tools_called: list[str]


class ErrorDetail(BaseModel):
    """Standard error envelope returned on all error responses."""

    status: str = "error"
    error_code: str
    message: str
    recoverable: bool
    details: dict[str, Any] | None = None
