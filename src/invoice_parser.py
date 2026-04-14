"""
Invoice field extraction using Claude's vision and document APIs.

Accepts PDF bytes, image bytes, or plain text and returns a structured
dict of invoice fields. Supports:
- PDF attachments (sent as base64 documents to Claude)
- Image attachments PNG/JPEG (sent as base64 images to Claude vision)
- Plain text / HTML email body (sent as text in the prompt)
"""

import base64
import json
import logging
import os
import re

import anthropic
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """You are an invoice data extraction assistant.
Extract the following fields from the provided invoice document or text.
Return ONLY a valid JSON object with these exact keys (use null for any field not found):

{
  "vendor_name": "<company or person that issued the invoice>",
  "invoice_number": "<invoice or bill number>",
  "invoice_date": "<date in YYYY-MM-DD format>",
  "due_date": "<due date in YYYY-MM-DD format>",
  "line_items": [
    {"description": "<item description>", "amount": <float>}
  ],
  "total_amount": <float>,
  "currency": "<3-letter ISO currency code, default USD>",
  "raw_text": "<a brief summary of the document content for audit>"
}

Rules:
- All monetary amounts must be plain floats (no currency symbols or commas).
- Dates must be YYYY-MM-DD strings or null.
- line_items must be an array even if there is only one item.
- Do not include any text outside the JSON object."""

_FALLBACK_RESULT: dict = {
    "vendor_name": None,
    "invoice_number": None,
    "invoice_date": None,
    "due_date": None,
    "line_items": [],
    "total_amount": 0.0,
    "currency": "USD",
    "raw_text": "",
}


def _build_content(content: bytes | str, mime_type: str) -> list[dict]:
    """Build the Anthropic API content list for the given input type.

    Args:
        content: Raw bytes for PDF/image or str for plain text.
        mime_type: MIME type string.

    Returns:
        List of content blocks for anthropic.messages.create.
    """
    if mime_type == "application/pdf":
        data_b64 = base64.standard_b64encode(content).decode()
        return [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": data_b64,
                },
            },
            {"type": "text", "text": _EXTRACTION_PROMPT},
        ]

    if mime_type in ("image/png", "image/jpeg", "image/jpg", "image/tiff"):
        # Normalize JPEG variants
        api_mime = "image/jpeg" if mime_type in ("image/jpeg", "image/jpg") else mime_type
        if api_mime == "image/tiff":
            # Claude doesn't support TIFF natively; send as text description
            return [{"type": "text", "text": f"[TIFF image attached — cannot parse]\n\n{_EXTRACTION_PROMPT}"}]
        data_b64 = base64.standard_b64encode(content).decode()
        return [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": api_mime,
                    "data": data_b64,
                },
            },
            {"type": "text", "text": _EXTRACTION_PROMPT},
        ]

    # Plain text / HTML / fallback
    text_content = content if isinstance(content, str) else content.decode("utf-8", errors="replace")
    return [{"type": "text", "text": f"{text_content}\n\n{_EXTRACTION_PROMPT}"}]


def parse_invoice(
    content: bytes | str,
    mime_type: str = "text/plain",
    source_email: str = "",
) -> dict:
    """Extract invoice fields from a PDF, image, or text using Claude.

    Args:
        content: Raw bytes (for PDF/image) or str (for plain text/HTML).
        mime_type: MIME type of the content. Supported:
            ``application/pdf``, ``image/png``, ``image/jpeg``,
            ``text/plain``, ``text/html``.
        source_email: The sender's email address, used for logging only.

    Returns:
        Dict with keys: vendor_name, invoice_number, invoice_date, due_date,
        line_items, total_amount, currency, raw_text.
        Any unextractable field is null / empty.
    """
    client = anthropic.Anthropic()
    content_blocks = _build_content(content, mime_type)
    model = os.getenv("INVOICE_PARSER_MODEL", "claude-sonnet-4-20250514")

    logger.info(
        "Parsing invoice mime_type=%s source_email=%s model=%s",
        mime_type,
        source_email,
        model,
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": content_blocks}],
        )
    except Exception:
        logger.exception("Claude API error during invoice parsing")
        return dict(_FALLBACK_RESULT)

    raw_output = response.content[0].text.strip()
    logger.debug("Invoice parser raw output: %s", raw_output[:200])

    # Extract JSON from the response (Claude may wrap it in markdown fences)
    json_match = re.search(r"\{.*\}", raw_output, re.DOTALL)
    if not json_match:
        logger.error("No JSON found in invoice parser output: %s", raw_output[:200])
        return dict(_FALLBACK_RESULT)

    try:
        parsed = json.loads(json_match.group())
    except json.JSONDecodeError:
        logger.error("Invalid JSON from invoice parser: %s", raw_output[:200])
        return dict(_FALLBACK_RESULT)

    # Merge with fallback to ensure all keys are present
    result = dict(_FALLBACK_RESULT)
    result.update({k: v for k, v in parsed.items() if k in _FALLBACK_RESULT})

    # Coerce total_amount to float
    try:
        result["total_amount"] = float(result["total_amount"] or 0)
    except (TypeError, ValueError):
        result["total_amount"] = 0.0

    # Ensure line_items is a list
    if not isinstance(result["line_items"], list):
        result["line_items"] = []

    logger.info(
        "Invoice parsed vendor=%s total=%s invoice_number=%s",
        result.get("vendor_name"),
        result.get("total_amount"),
        result.get("invoice_number"),
    )
    return result
