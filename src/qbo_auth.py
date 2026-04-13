"""
QuickBooks Online OAuth2 flow for sandbox testing.
Starts a local server, opens browser for authorization, and saves tokens.
"""

import json
import os
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("QBO_CLIENT_ID")
CLIENT_SECRET = os.getenv("QBO_CLIENT_SECRET")
REDIRECT_URI = os.getenv("QBO_REDIRECT_URI", "http://localhost:8080/callback")
ENVIRONMENT = os.getenv("QBO_ENVIRONMENT", "sandbox")

AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
SCOPES = "com.intuit.quickbooks.accounting"

TOKEN_FILE = os.path.join(os.path.dirname(__file__), "tokens.json")


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Handles the OAuth2 callback from Intuit."""

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/callback":
            params = parse_qs(parsed.query)
            if "code" in params:
                self.server.auth_code = params["code"][0]
                self.server.realm_id = params.get("realmId", [None])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<h1>Authorization successful!</h1>"
                    b"<p>You can close this window and go back to the terminal.</p>"
                )
            else:
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                error = params.get("error", ["unknown"])[0]
                self.wfile.write(f"<h1>Error: {error}</h1>".encode())
                self.server.auth_code = None
                self.server.realm_id = None
        self.server.should_stop = True

    def log_message(self, format, *args):
        pass  # Suppress default logging


def get_auth_url():
    """Build the Intuit OAuth2 authorization URL."""
    return (
        f"{AUTH_URL}"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={SCOPES}"
        f"&state=teststate"
    )


def exchange_code_for_tokens(auth_code, realm_id):
    """Exchange authorization code for access and refresh tokens."""
    response = requests.post(
        TOKEN_URL,
        auth=(CLIENT_ID, CLIENT_SECRET),
        headers={"Accept": "application/json"},
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": REDIRECT_URI,
        },
    )
    response.raise_for_status()
    tokens = response.json()
    tokens["realm_id"] = realm_id
    # Save tokens to file
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)
    print(f"Tokens saved to {TOKEN_FILE}")
    return tokens


def load_tokens():
    """Load saved tokens from file."""
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            return json.load(f)
    return None


def refresh_access_token(refresh_token):
    """Refresh an expired access token."""
    response = requests.post(
        TOKEN_URL,
        auth=(CLIENT_ID, CLIENT_SECRET),
        headers={"Accept": "application/json"},
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
    )
    response.raise_for_status()
    return response.json()


def run_oauth_flow():
    """Run the full OAuth2 flow: open browser, wait for callback, exchange tokens."""
    print("Starting OAuth2 flow...")

    # Start local server
    server = HTTPServer(("localhost", 8080), OAuthCallbackHandler)
    server.auth_code = None
    server.realm_id = None
    server.should_stop = False

    # Open browser
    auth_url = get_auth_url()
    print(f"\nOpening browser for authorization...")
    print(f"If it doesn't open, go to:\n{auth_url}\n")
    webbrowser.open(auth_url)

    # Wait for callback
    while not server.should_stop:
        server.handle_request()

    if server.auth_code:
        print(f"Got authorization code. Realm ID: {server.realm_id}")
        tokens = exchange_code_for_tokens(server.auth_code, server.realm_id)
        print("OAuth2 flow complete!")
        return tokens
    else:
        print("Authorization failed.")
        return None


if __name__ == "__main__":
    tokens = run_oauth_flow()
    if tokens:
        print(f"\nAccess Token: {tokens['access_token'][:20]}...")
        print(f"Realm ID: {tokens.get('realm_id')}")
