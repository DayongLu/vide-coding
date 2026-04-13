"""
Integration tests for the Finance Agent API.

Uses FastAPI TestClient with an in-memory SQLite database and mocked
Anthropic + qbo_client calls. No live network calls.
"""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture(autouse=True)
def set_api_key(monkeypatch):
    monkeypatch.setenv("API_KEY", "test-key")


@pytest.fixture()
def client(tmp_path):
    """TestClient wired to an isolated per-test SQLite file."""
    from api.main import create_app
    db_path = tmp_path / "test.db"
    app = create_app(db_path=db_path)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


AUTH = {"Authorization": "Bearer test-key"}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health_returns_200(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_no_auth_required(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def test_missing_auth_returns_401(client):
    resp = client.post("/api/v1/conversations")
    assert resp.status_code == 401


def test_wrong_auth_returns_401(client):
    resp = client.post(
        "/api/v1/conversations",
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Conversation CRUD
# ---------------------------------------------------------------------------

def test_create_conversation_returns_201(client):
    resp = client.post("/api/v1/conversations", headers=AUTH)
    assert resp.status_code == 201
    body = resp.json()
    assert "id" in body
    assert body["messages"] == []


def test_list_conversations_returns_200(client):
    client.post("/api/v1/conversations", headers=AUTH)
    client.post("/api/v1/conversations", headers=AUTH)
    resp = client.get("/api/v1/conversations", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["conversations"]) == 2


def test_list_conversations_pagination(client):
    for _ in range(5):
        client.post("/api/v1/conversations", headers=AUTH)
    resp = client.get("/api/v1/conversations?limit=3", headers=AUTH)
    body = resp.json()
    assert len(body["conversations"]) == 3
    assert body["next_cursor"] is not None

    resp2 = client.get(
        f"/api/v1/conversations?limit=3&cursor={body['next_cursor']}", headers=AUTH
    )
    body2 = resp2.json()
    assert len(body2["conversations"]) == 2
    assert body2["next_cursor"] is None


def test_get_conversation_returns_200(client):
    conv = client.post("/api/v1/conversations", headers=AUTH).json()
    resp = client.get(f"/api/v1/conversations/{conv['id']}", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["id"] == conv["id"]


def test_get_unknown_conversation_returns_404(client):
    resp = client.get("/api/v1/conversations/does-not-exist", headers=AUTH)
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"]["error_code"] == "CONVERSATION_NOT_FOUND"


def test_delete_conversation_returns_204(client):
    conv = client.post("/api/v1/conversations", headers=AUTH).json()
    resp = client.delete(f"/api/v1/conversations/{conv['id']}", headers=AUTH)
    assert resp.status_code == 204
    resp2 = client.get(f"/api/v1/conversations/{conv['id']}", headers=AUTH)
    assert resp2.status_code == 404


def test_delete_unknown_conversation_returns_404(client):
    resp = client.delete("/api/v1/conversations/ghost", headers=AUTH)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Send message — mocked streaming
# ---------------------------------------------------------------------------

def _mock_stream_events(text="Hello from Claude"):
    """Build a fake async generator that yields SSE-like chunks."""
    async def _gen(*args, **kwargs):
        yield f"event: token\ndata: {json.dumps({'text': text})}\n\n"
        yield f"event: done\ndata: {json.dumps({'conversation_id': 'x', 'tools_called': [], 'full_text': text})}\n\n"
    return _gen


def test_send_message_to_unknown_conversation_returns_404(client):
    resp = client.post(
        "/api/v1/conversations/ghost/messages",
        json={"message": "hi"},
        headers=AUTH,
    )
    assert resp.status_code == 404


def test_send_blank_message_returns_400(client):
    conv = client.post("/api/v1/conversations", headers=AUTH).json()
    resp = client.post(
        f"/api/v1/conversations/{conv['id']}/messages",
        json={"message": ""},
        headers=AUTH,
    )
    assert resp.status_code == 400


def test_send_message_too_long_returns_400(client):
    conv = client.post("/api/v1/conversations", headers=AUTH).json()
    resp = client.post(
        f"/api/v1/conversations/{conv['id']}/messages",
        json={"message": "x" * 10_001},
        headers=AUTH,
    )
    assert resp.status_code == 400


def test_send_message_returns_sse_stream(client):
    conv = client.post("/api/v1/conversations", headers=AUTH).json()

    async def fake_run_agent_turn(conv_id, messages, db):
        yield f"event: token\ndata: {json.dumps({'text': 'Hi'})}\n\n"
        yield f"event: done\ndata: {json.dumps({'conversation_id': conv_id, 'tools_called': [], 'full_text': 'Hi'})}\n\n"

    with patch("api.routers.conversations.run_agent_turn", fake_run_agent_turn):
        resp = client.post(
            f"/api/v1/conversations/{conv['id']}/messages",
            json={"message": "Hello"},
            headers=AUTH,
        )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    assert "event: token" in resp.text
    assert "event: done" in resp.text
