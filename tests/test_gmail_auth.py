"""Tests for src/gmail_auth.py — OAuth token load/save."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import gmail_auth


@pytest.fixture()
def token_paths(tmp_path, monkeypatch):
    """Point gmail_auth at temp credentials/token files."""
    creds_path = tmp_path / "gmail_credentials.json"
    token_path = tmp_path / "gmail_tokens.json"
    monkeypatch.setattr(gmail_auth, "CREDENTIALS_FILE", str(creds_path))
    monkeypatch.setattr(gmail_auth, "TOKEN_FILE", str(token_path))
    return creds_path, token_path


# ---------------------------------------------------------------------------
# load_credentials
# ---------------------------------------------------------------------------


def test_load_credentials_raises_when_token_file_missing(token_paths):
    """load_credentials raises FileNotFoundError if token file is absent."""
    with pytest.raises(FileNotFoundError, match="Gmail token file not found"):
        gmail_auth.load_credentials()


def test_load_credentials_reads_token_file(token_paths):
    """load_credentials hydrates a Credentials object from the JSON token file."""
    _, token_path = token_paths
    token_path.write_text(json.dumps({
        "token": "access-tok",
        "refresh_token": "refresh-tok",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "cid",
        "client_secret": "csecret",
        "scopes": gmail_auth.SCOPES,
    }))

    creds = gmail_auth.load_credentials()
    assert creds.token == "access-tok"
    assert creds.refresh_token == "refresh-tok"
    assert creds.client_id == "cid"


def test_load_credentials_defaults_scopes_when_missing(token_paths):
    """load_credentials falls back to module SCOPES when scopes key is absent."""
    _, token_path = token_paths
    token_path.write_text(json.dumps({
        "token": "t",
        "refresh_token": "r",
        "token_uri": "uri",
        "client_id": "cid",
        "client_secret": "cs",
    }))

    creds = gmail_auth.load_credentials()
    assert list(creds.scopes) == gmail_auth.SCOPES


# ---------------------------------------------------------------------------
# authenticate
# ---------------------------------------------------------------------------


def test_authenticate_raises_when_credentials_file_missing(token_paths):
    """authenticate raises FileNotFoundError if the OAuth client file is absent."""
    with pytest.raises(FileNotFoundError, match="Gmail credentials file not found"):
        gmail_auth.authenticate()


def test_authenticate_writes_token_file(token_paths):
    """authenticate runs the OAuth flow and persists tokens to TOKEN_FILE."""
    creds_path, token_path = token_paths
    creds_path.write_text("{}")  # exists; content does not matter (flow is mocked)

    fake_creds = MagicMock()
    fake_creds.token = "new-access"
    fake_creds.refresh_token = "new-refresh"
    fake_creds.token_uri = "https://oauth2.googleapis.com/token"
    fake_creds.client_id = "client-id"
    fake_creds.client_secret = "client-secret"
    fake_creds.scopes = gmail_auth.SCOPES

    fake_flow = MagicMock()
    fake_flow.run_local_server.return_value = fake_creds

    with patch.object(
        gmail_auth.InstalledAppFlow,
        "from_client_secrets_file",
        return_value=fake_flow,
    ):
        result = gmail_auth.authenticate()

    assert result is fake_creds
    assert token_path.exists()
    saved = json.loads(token_path.read_text())
    assert saved["token"] == "new-access"
    assert saved["refresh_token"] == "new-refresh"
    assert saved["scopes"] == gmail_auth.SCOPES
