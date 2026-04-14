"""
Gmail OAuth2 authentication flow for the Finance Agent.

Run this script once to authorize access to Gmail:

    python3 src/gmail_auth.py

Opens a browser window for Google account authorization and saves credentials
to ``gmail_tokens.json`` (gitignored). The email client then loads those
tokens on each run without requiring re-authentication.

Scopes requested:
- gmail.readonly  — read emails and attachments
- gmail.modify    — add/remove labels (to mark emails as processed)
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

CREDENTIALS_FILE = os.getenv(
    "GMAIL_CREDENTIALS_FILE",
    str(Path(__file__).parent / "gmail_credentials.json"),
)
TOKEN_FILE = os.getenv(
    "GMAIL_TOKEN_FILE",
    str(Path(__file__).parent / "gmail_tokens.json"),
)


def authenticate() -> Credentials:
    """Run the OAuth2 browser flow and save tokens to TOKEN_FILE.

    Returns:
        Authorized Credentials object.

    Raises:
        FileNotFoundError: If CREDENTIALS_FILE does not exist.
    """
    if not Path(CREDENTIALS_FILE).exists():
        raise FileNotFoundError(
            f"Gmail credentials file not found: {CREDENTIALS_FILE}\n"
            "Download it from Google Cloud Console → APIs & Services → Credentials."
        )

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if creds.scopes else SCOPES,
    }
    Path(TOKEN_FILE).write_text(json.dumps(token_data, indent=2))
    print(f"Gmail tokens saved to {TOKEN_FILE}")
    return creds


def load_credentials() -> Credentials:
    """Load stored Gmail OAuth credentials from TOKEN_FILE.

    Returns:
        Credentials object ready for use with the Gmail API.

    Raises:
        FileNotFoundError: If TOKEN_FILE does not exist (run gmail_auth.py first).
    """
    if not Path(TOKEN_FILE).exists():
        raise FileNotFoundError(
            f"Gmail token file not found: {TOKEN_FILE}\n"
            "Run `python3 src/gmail_auth.py` to authorize Gmail access."
        )

    data = json.loads(Path(TOKEN_FILE).read_text())
    return Credentials(
        token=data["token"],
        refresh_token=data["refresh_token"],
        token_uri=data["token_uri"],
        client_id=data["client_id"],
        client_secret=data["client_secret"],
        scopes=data.get("scopes", SCOPES),
    )


if __name__ == "__main__":
    creds = authenticate()
    print("Gmail authentication successful.")
    print(f"Token expires: {creds.expiry}")
