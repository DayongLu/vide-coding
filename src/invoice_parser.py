"""
Invoice field extraction using the configured LLM provider.

Accepts PDF bytes, image bytes, or plain text and returns a structured
dict of invoice fields. The provider is selected via the ``LLM_PROVIDER``
environment variable (``anthropic`` by default, ``gemini``, or ``openai``).

Supported input types:
- PDF attachments (base64 document or inline data depending on provider)
- Image attachments PNG/JPEG (base64 vision input)
- Plain text / HTML email body (text prompt)
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


# ---------------------------------------------------------------------------
# Provider-specific content builders and callers
# ---------------------------------------------------------------------------


def _build_anthropic_content(content: bytes | str, mime_type: str) -> list[dict]:
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


def _call_anthropic(content: bytes | str, mime_type: str) -> str:
    """Call Anthropic Claude (sync) and return the raw response text.

    Uses the ``INVOICE_PARSER_MODEL`` env var (default: claude-sonnet-4-20250514).
    """
    client = anthropic.Anthropic()
    model = os.getenv("INVOICE_PARSER_MODEL", "claude-sonnet-4-20250514")
    blocks = _build_anthropic_content(content, mime_type)
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": blocks}],
    )
    return response.content[0].text


def _call_gemini(content: bytes | str, mime_type: str) -> str:
    """Call Google Gemini (sync) and return the raw response text.

    Uses the ``INVOICE_PARSER_MODEL`` env var (default: gemini-2.0-flash).
    Requires ``google-generativeai`` package and ``GEMINI_API_KEY`` env var.

    PDFs and images are sent as inline data parts. TIFF files fall back to a
    text description since Gemini does not support TIFF natively.
    """
    import google.generativeai as genai  # noqa: PLC0415

    model_name = os.getenv("INVOICE_PARSER_MODEL", "gemini-2.0-flash")
    model = genai.GenerativeModel(model_name)

    if mime_type == "image/tiff":
        response = model.generate_content(
            f"[TIFF image attached — cannot parse]\n\n{_EXTRACTION_PROMPT}"
        )
    elif mime_type in ("application/pdf", "image/png", "image/jpeg", "image/jpg"):
        api_mime = "image/jpeg" if mime_type in ("image/jpeg", "image/jpg") else mime_type
        response = model.generate_content(
            [{"mime_type": api_mime, "data": content}, _EXTRACTION_PROMPT]
        )
    else:
        text = content if isinstance(content, str) else content.decode("utf-8", errors="replace")
        response = model.generate_content(f"{text}\n\n{_EXTRACTION_PROMPT}")

    return response.text


def _call_openai(content: bytes | str, mime_type: str) -> str:
    """Call OpenAI-compatible API (sync) and return the raw response text.

    Uses the ``INVOICE_PARSER_MODEL`` env var (default: gpt-4o).
    Requires ``openai`` package and ``OPENAI_API_KEY`` env var.

    Images are sent as base64 data URIs. PDFs are not supported for inline
    upload via the standard chat completions API — a text-only fallback prompt
    is sent instead.
    """
    import openai  # noqa: PLC0415

    model_name = os.getenv("INVOICE_PARSER_MODEL", "gpt-4o")
    client = openai.OpenAI()

    if mime_type.startswith("image/") and mime_type != "image/tiff":
        api_mime = "image/jpeg" if mime_type in ("image/jpeg", "image/jpg") else mime_type
        b64 = base64.standard_b64encode(content).decode()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{api_mime};base64,{b64}"}},
                    {"type": "text", "text": _EXTRACTION_PROMPT},
                ],
            }
        ]
    else:
        if mime_type == "application/pdf":
            prompt = f"[PDF document — inline PDF not supported by this provider. Extract what you can from any metadata.]\n\n{_EXTRACTION_PROMPT}"
        else:
            text = content if isinstance(content, str) else content.decode("utf-8", errors="replace")
            prompt = f"{text}\n\n{_EXTRACTION_PROMPT}"
        messages = [{"role": "user", "content": prompt}]

    response = client.chat.completions.create(
        model=model_name,
        max_tokens=1024,
        messages=messages,
    )
    return response.choices[0].message.content


def _call_llm(content: bytes | str, mime_type: str) -> str:
    """Route invoice extraction to the active LLM provider.

    Reads ``LLM_PROVIDER`` env var. Supported values:
    - ``anthropic`` (default) — Claude via Anthropic API
    - ``gemini`` — Google Gemini via google-generativeai
    - ``openai`` — OpenAI (or compatible) via openai package

    Args:
        content: Raw bytes (PDF/image) or str (plain text/HTML).
        mime_type: MIME type of the content.

    Returns:
        Raw LLM response string (unparsed JSON or text).
    """
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    if provider == "gemini":
        return _call_gemini(content, mime_type)
    if provider == "openai":
        return _call_openai(content, mime_type)
    return _call_anthropic(content, mime_type)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_invoice(
    content: bytes | str,
    mime_type: str = "text/plain",
    source_email: str = "",
) -> dict:
    """Extract invoice fields from a PDF, image, or text using the configured LLM.

    The LLM provider is selected via the ``LLM_PROVIDER`` environment variable
    (default: ``anthropic``).

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
    provider = os.getenv("LLM_PROVIDER", "anthropic")
    logger.info(
        "Parsing invoice mime_type=%s source_email=%s provider=%s",
        mime_type,
        source_email,
        provider,
    )

    try:
        raw_output = _call_llm(content, mime_type).strip()
    except Exception:
        logger.exception("LLM API error during invoice parsing")
        return dict(_FALLBACK_RESULT)

    logger.debug("Invoice parser raw output: %s", raw_output[:200])

    # Extract JSON from the response (LLM may wrap it in markdown fences)
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
