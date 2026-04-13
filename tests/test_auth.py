"""Tests for src/api/auth.py."""

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api.auth import verify_api_key
from fastapi import Depends


@pytest.fixture()
def app_with_auth():
    app = FastAPI()

    @app.get("/protected")
    def protected(auth=Depends(verify_api_key)):
        return {"ok": True}

    return app


def test_valid_key_passes(app_with_auth, monkeypatch):
    monkeypatch.setenv("API_KEY", "test-secret")
    client = TestClient(app_with_auth, raise_server_exceptions=False)
    resp = client.get("/protected", headers={"Authorization": "Bearer test-secret"})
    assert resp.status_code == 200


def test_invalid_key_returns_401(app_with_auth, monkeypatch):
    monkeypatch.setenv("API_KEY", "test-secret")
    client = TestClient(app_with_auth, raise_server_exceptions=False)
    resp = client.get("/protected", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_missing_header_returns_401(app_with_auth, monkeypatch):
    monkeypatch.setenv("API_KEY", "test-secret")
    client = TestClient(app_with_auth, raise_server_exceptions=False)
    resp = client.get("/protected")
    assert resp.status_code == 401


def test_401_response_has_error_envelope(app_with_auth, monkeypatch):
    monkeypatch.setenv("API_KEY", "test-secret")
    client = TestClient(app_with_auth, raise_server_exceptions=False)
    resp = client.get("/protected", headers={"Authorization": "Bearer bad"})
    body = resp.json()
    assert body["detail"]["error_code"] == "UNAUTHORIZED"
    assert body["detail"]["recoverable"] is False
