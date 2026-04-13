"""
Conversation CRUD and message endpoints for the Finance Agent API.
"""

import base64
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

import api.db as db_module
from api.agent import run_agent_turn
from api.auth import verify_api_key
from api.errors import api_error
from api.models import (
    ConversationListItem,
    ConversationListResponse,
    ConversationResponse,
    MessageResponse,
    SendMessageRequest,
    SendMessageResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/conversations",
    tags=["conversations"],
    dependencies=[Depends(verify_api_key)],
)


def _now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _row_to_message(row: sqlite3.Row) -> MessageResponse:
    """Convert a messages table row to a MessageResponse.

    Args:
        row: SQLite row with id, role, content_json, timestamp columns.

    Returns:
        MessageResponse with content extracted from content_json.
    """
    content_data = json.loads(row["content_json"])
    # content_json may be a plain string (user messages) or a list of blocks.
    if isinstance(content_data, str):
        content = content_data
    elif isinstance(content_data, list):
        content = " ".join(
            block.get("text", "") for block in content_data if isinstance(block, dict)
        )
    else:
        content = str(content_data)
    return MessageResponse(
        id=row["id"],
        role=row["role"],
        content=content,
        timestamp=row["timestamp"],
    )


# ---------------------------------------------------------------------------
# POST /conversations — create
# ---------------------------------------------------------------------------


@router.post("", status_code=201, response_model=ConversationResponse)
async def create_conversation(
    db: sqlite3.Connection = Depends(db_module.get_db),
) -> ConversationResponse:
    """Create a new empty conversation.

    Returns:
        The created conversation with an empty message list.
    """
    conv_id = str(uuid.uuid4())
    now = _now()
    db.execute(
        "INSERT INTO conversations (id, created_at, updated_at) VALUES (?, ?, ?)",
        (conv_id, now, now),
    )
    db.commit()
    return ConversationResponse(
        id=conv_id, created_at=now, updated_at=now, messages=[]
    )


# ---------------------------------------------------------------------------
# GET /conversations — list
# ---------------------------------------------------------------------------


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    limit: int = 20,
    cursor: str | None = None,
    db: sqlite3.Connection = Depends(db_module.get_db),
) -> ConversationListResponse:
    """List conversations in reverse-chronological order with cursor pagination.

    Args:
        limit: Maximum results to return (1–100).
        cursor: Opaque pagination cursor from a previous response.

    Returns:
        Paginated conversation list with optional ``next_cursor``.
    """
    limit = max(1, min(limit, 100))

    # Decode cursor: base64-encoded "updated_at|id"
    if cursor:
        try:
            decoded = base64.b64decode(cursor.encode()).decode()
            cursor_updated_at, cursor_id = decoded.split("|", 1)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid cursor")
        rows = db.execute(
            """
            SELECT id, created_at, updated_at FROM conversations
            WHERE (updated_at, id) < (?, ?)
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (cursor_updated_at, cursor_id, limit + 1),
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT id, created_at, updated_at FROM conversations
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (limit + 1,),
        ).fetchall()

    total = db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]

    has_more = len(rows) > limit
    page = rows[:limit]

    next_cursor = None
    if has_more:
        last = page[-1]
        raw = f"{last['updated_at']}|{last['id']}"
        next_cursor = base64.b64encode(raw.encode()).decode()

    # Build preview from first user message
    items: list[ConversationListItem] = []
    for row in page:
        first_user = db.execute(
            """
            SELECT content_json FROM messages
            WHERE conversation_id = ? AND role = 'user' AND is_internal = 0
            ORDER BY timestamp ASC LIMIT 1
            """,
            (row["id"],),
        ).fetchone()
        preview = ""
        if first_user:
            raw_content = json.loads(first_user["content_json"])
            preview_text = raw_content if isinstance(raw_content, str) else str(raw_content)
            preview = preview_text[:100]
        items.append(
            ConversationListItem(
                id=row["id"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                preview=preview,
            )
        )

    return ConversationListResponse(
        conversations=items, next_cursor=next_cursor, total=total
    )


# ---------------------------------------------------------------------------
# GET /conversations/{id} — retrieve
# ---------------------------------------------------------------------------


@router.get("/{conv_id}", response_model=ConversationResponse)
async def get_conversation(
    conv_id: str,
    db: sqlite3.Connection = Depends(db_module.get_db),
) -> ConversationResponse:
    """Retrieve a conversation with its non-internal message history.

    Args:
        conv_id: Conversation UUID.

    Returns:
        ConversationResponse with visible messages only.

    Raises:
        HTTPException: 404 if not found.
    """
    row = db.execute(
        "SELECT id, created_at, updated_at FROM conversations WHERE id = ?",
        (conv_id,),
    ).fetchone()
    if not row:
        raise HTTPException(
            status_code=404,
            detail={
                "status": "error",
                "error_code": "CONVERSATION_NOT_FOUND",
                "message": f"Conversation '{conv_id}' not found.",
                "recoverable": False,
            },
        )

    msg_rows = db.execute(
        """
        SELECT id, role, content_json, timestamp FROM messages
        WHERE conversation_id = ? AND is_internal = 0
        ORDER BY timestamp ASC
        """,
        (conv_id,),
    ).fetchall()

    return ConversationResponse(
        id=row["id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        messages=[_row_to_message(r) for r in msg_rows],
    )


# ---------------------------------------------------------------------------
# DELETE /conversations/{id}
# ---------------------------------------------------------------------------


@router.delete("/{conv_id}", status_code=204)
async def delete_conversation(
    conv_id: str,
    db: sqlite3.Connection = Depends(db_module.get_db),
) -> None:
    """Delete a conversation and all its messages.

    Args:
        conv_id: Conversation UUID.

    Raises:
        HTTPException: 404 if not found.
    """
    row = db.execute(
        "SELECT id FROM conversations WHERE id = ?", (conv_id,)
    ).fetchone()
    if not row:
        raise HTTPException(
            status_code=404,
            detail={
                "status": "error",
                "error_code": "CONVERSATION_NOT_FOUND",
                "message": f"Conversation '{conv_id}' not found.",
                "recoverable": False,
            },
        )
    db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    db.commit()


# ---------------------------------------------------------------------------
# POST /conversations/{id}/messages — send message (streaming or JSON)
# ---------------------------------------------------------------------------


@router.post("/{conv_id}/messages", response_model=None)
async def send_message(
    conv_id: str,
    body: SendMessageRequest,
    db: sqlite3.Connection = Depends(db_module.get_db),
) -> StreamingResponse:
    """Send a user message and stream or return the assistant response.

    If the ``Accept`` header is ``application/json``, runs the agent loop
    synchronously and returns a ``SendMessageResponse``. Otherwise returns
    an SSE stream.

    Args:
        conv_id: Conversation UUID.
        body: Request body with the user message.

    Returns:
        StreamingResponse (SSE) or JSONResponse depending on Accept header.

    Raises:
        HTTPException: 404 if conversation not found.
    """
    # Verify conversation exists
    row = db.execute(
        "SELECT id FROM conversations WHERE id = ?", (conv_id,)
    ).fetchone()
    if not row:
        raise HTTPException(
            status_code=404,
            detail={
                "status": "error",
                "error_code": "CONVERSATION_NOT_FOUND",
                "message": f"Conversation '{conv_id}' not found.",
                "recoverable": False,
            },
        )

    # Persist user message
    msg_id = str(uuid.uuid4())
    now = _now()
    db.execute(
        "INSERT INTO messages (id, conversation_id, role, content_json, timestamp, is_internal) VALUES (?,?,?,?,?,?)",
        (msg_id, conv_id, "user", json.dumps(body.message), now, 0),
    )
    db.commit()

    # Load full history for the agent (including internal rows)
    all_rows = db.execute(
        """
        SELECT role, content_json FROM messages
        WHERE conversation_id = ?
        ORDER BY timestamp ASC
        """,
        (conv_id,),
    ).fetchall()

    messages = _build_anthropic_messages(all_rows)

    async def event_stream():
        async for chunk in run_agent_turn(conv_id, messages, db):
            yield chunk

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _build_anthropic_messages(rows) -> list[dict]:
    """Convert message rows into the Anthropic messages format.

    Args:
        rows: SQLite rows with role and content_json columns.

    Returns:
        List of message dicts for the Anthropic API.
    """
    messages = []
    for row in rows:
        content = json.loads(row["content_json"])
        messages.append({"role": row["role"] if row["role"] in ("user", "assistant") else "user", "content": content})
    return messages
