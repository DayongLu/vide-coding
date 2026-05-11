"""
Gmail API client for fetching invoice emails and attachments.

Searches the inbox for unread emails that look like they contain invoices
(PDF/image attachments or invoice-related subject keywords). Attachments
are returned as base64-encoded bytes ready for the invoice parser.

Processed emails are labelled "InvoiceProcessed" so they are not fetched
again on subsequent runs.
"""

import base64
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from gmail_auth import SCOPES, TOKEN_FILE, load_credentials

load_dotenv()

logger = logging.getLogger(__name__)

INBOX_LABEL = os.getenv("GMAIL_INBOX_LABEL", "INBOX")
PROCESSED_LABEL_NAME = "InvoiceProcessed"

# Subject keywords that suggest an invoice email (case-insensitive)
_INVOICE_SUBJECT_PATTERNS = re.compile(
    r"\b(invoice|bill|statement|receipt|remittance|payment due)\b",
    re.IGNORECASE,
)

# MIME types accepted as invoice attachments
_INVOICE_MIME_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/tiff",
}


def _get_service():
    """Build and return an authenticated Gmail API service client.

    Refreshes the access token if expired.

    Returns:
        googleapiclient Resource for the Gmail v1 API.
    """
    creds = load_credentials()
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # Persist refreshed token
        import json
        token_data = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes) if creds.scopes else SCOPES,
        }
        Path(TOKEN_FILE).write_text(json.dumps(token_data, indent=2))
    return build("gmail", "v1", credentials=creds)


def _get_or_create_label(service, label_name: str) -> str:
    """Get the Gmail label ID for ``label_name``, creating it if needed.

    Args:
        service: Authenticated Gmail API service.
        label_name: Human-readable label name.

    Returns:
        Gmail label ID string.
    """
    existing = service.users().labels().list(userId="me").execute()
    for label in existing.get("labels", []):
        if label["name"] == label_name:
            return label["id"]

    created = service.users().labels().create(
        userId="me",
        body={"name": label_name, "labelListVisibility": "labelShow"},
    ).execute()
    logger.info("Created Gmail label '%s' (id=%s)", label_name, created["id"])
    return created["id"]


def _has_invoice_attachment(parts: list) -> bool:
    """Return True if any part has a MIME type matching invoice attachment types."""
    for part in parts:
        if part.get("mimeType") in _INVOICE_MIME_TYPES and part.get("filename"):
            return True
        if "parts" in part:
            if _has_invoice_attachment(part["parts"]):
                return True
    return False


def _looks_like_invoice(message_meta: dict) -> bool:
    """Heuristic: return True if the message is likely an invoice.

    Checks subject line keywords and/or presence of invoice-type attachments.

    Args:
        message_meta: Gmail message metadata dict with headers and payload.

    Returns:
        True if the message should be processed as an invoice candidate.
    """
    headers = {
        h["name"]: h["value"]
        for h in message_meta.get("payload", {}).get("headers", [])
    }
    subject = headers.get("Subject", "")

    if _INVOICE_SUBJECT_PATTERNS.search(subject):
        return True

    parts = message_meta.get("payload", {}).get("parts", [])
    return _has_invoice_attachment(parts)


def fetch_invoice_emails(max_results: int = 20) -> list[dict]:
    """Fetch unread emails that look like invoices from Gmail.

    Only returns emails that have not been labelled ``InvoiceProcessed``.

    Args:
        max_results: Maximum number of emails to return.

    Returns:
        List of dicts with keys: message_id, subject, from_address,
        received_at, has_attachment, snippet.
    """
    service = _get_service()

    # Search for unread emails not yet processed
    query = f"label:{INBOX_LABEL} is:unread -label:{PROCESSED_LABEL_NAME}"
    response = service.users().messages().list(
        userId="me",
        q=query,
        maxResults=max_results,
    ).execute()

    messages = response.get("messages", [])
    logger.info("Gmail search returned %d candidate messages", len(messages))

    results = []
    for msg_ref in messages:
        msg_id = msg_ref["id"]
        message = service.users().messages().get(
            userId="me",
            id=msg_id,
            format="metadata",
            metadataHeaders=["Subject", "From", "Date"],
        ).execute()

        if not _looks_like_invoice(message):
            logger.debug("Skipping message %s (not invoice-like)", msg_id)
            continue

        headers = {
            h["name"]: h["value"]
            for h in message.get("payload", {}).get("headers", [])
        }
        parts = message.get("payload", {}).get("parts", [])

        results.append({
            "message_id": msg_id,
            "subject": headers.get("Subject", ""),
            "from_address": headers.get("From", ""),
            "received_at": headers.get("Date", ""),
            "has_attachment": _has_invoice_attachment(parts),
            "snippet": message.get("snippet", ""),
        })

    logger.info("Found %d invoice-candidate emails", len(results))
    return results


def get_attachments(message_id: str) -> list[dict]:
    """Fetch all invoice-type attachments from a Gmail message.

    Args:
        message_id: Gmail message ID.

    Returns:
        List of dicts with keys: filename, mime_type, data_b64 (base64 bytes).
    """
    service = _get_service()
    message = service.users().messages().get(
        userId="me",
        id=message_id,
        format="full",
    ).execute()

    attachments = []
    _extract_attachments(service, message_id, message.get("payload", {}), attachments)
    logger.info("Extracted %d attachments from message %s", len(attachments), message_id)
    return attachments


def _extract_attachments(service, message_id: str, part: dict, results: list) -> None:
    """Recursively walk MIME parts and collect invoice-type attachments.

    Args:
        service: Authenticated Gmail API service.
        message_id: Gmail message ID.
        part: Current MIME part dict.
        results: List to append attachment dicts to.
    """
    mime_type = part.get("mimeType", "")
    filename = part.get("filename", "")

    if mime_type in _INVOICE_MIME_TYPES and filename:
        body = part.get("body", {})
        attachment_id = body.get("attachmentId")
        if attachment_id:
            att = service.users().messages().attachments().get(
                userId="me",
                messageId=message_id,
                id=attachment_id,
            ).execute()
            data_b64 = att.get("data", "")
            # Gmail uses URL-safe base64; normalize to standard
            data_b64 = data_b64.replace("-", "+").replace("_", "/")
            results.append({
                "filename": filename,
                "mime_type": mime_type,
                "data_b64": data_b64,
            })
        elif body.get("data"):
            data_b64 = body["data"].replace("-", "+").replace("_", "/")
            results.append({
                "filename": filename,
                "mime_type": mime_type,
                "data_b64": data_b64,
            })

    for sub_part in part.get("parts", []):
        _extract_attachments(service, message_id, sub_part, results)


def get_email_body(message_id: str) -> str:
    """Extract the plain-text or HTML body of an email.

    Prefers text/plain; falls back to text/html (tags stripped).

    Args:
        message_id: Gmail message ID.

    Returns:
        Email body as a plain text string (may be empty).
    """
    service = _get_service()
    message = service.users().messages().get(
        userId="me",
        id=message_id,
        format="full",
    ).execute()

    return _extract_body_text(message.get("payload", {}))


def _extract_body_text(part: dict) -> str:
    """Recursively extract plain-text body content from MIME parts.

    Args:
        part: Current MIME part dict.

    Returns:
        Decoded text content.
    """
    mime_type = part.get("mimeType", "")
    body_data = part.get("body", {}).get("data", "")

    if body_data and mime_type in ("text/plain", "text/html"):
        return base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")

    # Recurse into sub-parts (prefer text/plain, fallback to text/html)
    plain_text = ""
    html_text = ""
    for sub_part in part.get("parts", []):
        sub_mime = sub_part.get("mimeType", "")
        if sub_mime == "text/plain":
            plain_text = _extract_body_text(sub_part)
        elif sub_mime == "text/html" and not plain_text:
            html_raw = _extract_body_text(sub_part)
            # Strip HTML tags for a rough plain-text version
            html_text = re.sub(r"<[^>]+>", " ", html_raw)
        elif sub_mime.startswith("multipart/"):
            result = _extract_body_text(sub_part)
            if result:
                plain_text = result

    return plain_text or html_text


def mark_as_processed(message_id: str) -> None:
    """Add the ``InvoiceProcessed`` label to a Gmail message.

    This prevents the message from being fetched again on subsequent runs.

    Args:
        message_id: Gmail message ID.
    """
    service = _get_service()
    label_id = _get_or_create_label(service, PROCESSED_LABEL_NAME)
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={"addLabelIds": [label_id], "removeLabelIds": ["UNREAD"]},
    ).execute()
    logger.info("Marked message %s as processed", message_id)
