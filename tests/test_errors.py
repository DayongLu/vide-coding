"""Tests for src/api/errors.py."""

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api.errors import api_error, register_exception_handlers


def test_api_error_returns_correct_shape():
    resp = api_error(400, "BAD_INPUT", "Something wrong", True)
    body = resp.body
    import json
    data = json.loads(body)
    assert data["status"] == "error"
    assert data["error_code"] == "BAD_INPUT"
    assert data["message"] == "Something wrong"
    assert data["recoverable"] is True
    assert resp.status_code == 400


def _make_app():
    app = FastAPI()
    register_exception_handlers(app)

    class Item(BaseModel):
        name: str

    @app.post("/items")
    def create_item(item: Item):
        return item

    @app.get("/boom")
    def boom():
        raise RuntimeError("oops")

    return app


def test_validation_error_returns_400():
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/items", json={"wrong_field": "x"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["error_code"] == "VALIDATION_ERROR"
    assert body["recoverable"] is True


def test_unhandled_exception_returns_500():
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/boom")
    assert resp.status_code == 500
    body = resp.json()
    assert body["error_code"] == "INTERNAL_ERROR"
    assert body["recoverable"] is True
