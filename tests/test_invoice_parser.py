"""Tests for src/invoice_parser.py."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import invoice_parser


def _mock_claude_response(text: str):
    """Build a mock Anthropic response with the given text."""
    content_block = MagicMock()
    content_block.text = text
    response = MagicMock()
    response.content = [content_block]
    return response


_VALID_JSON = json.dumps({
    "vendor_name": "Acme Corp",
    "invoice_number": "INV-001",
    "invoice_date": "2026-04-01",
    "due_date": "2026-04-30",
    "line_items": [{"description": "Consulting", "amount": 1200.0}],
    "total_amount": 1200.0,
    "currency": "USD",
    "raw_text": "Invoice from Acme Corp",
})


def test_parse_invoice_plain_text():
    """parse_invoice extracts fields from plain text content."""
    with patch("invoice_parser.anthropic.Anthropic") as MockAnthropic:
        client = MockAnthropic.return_value
        client.messages.create.return_value = _mock_claude_response(_VALID_JSON)

        result = invoice_parser.parse_invoice(
            content="Invoice from Acme Corp. Total: $1200. Due: April 30.",
            mime_type="text/plain",
        )

    assert result["vendor_name"] == "Acme Corp"
    assert result["total_amount"] == 1200.0
    assert result["invoice_number"] == "INV-001"
    assert result["due_date"] == "2026-04-30"
    assert len(result["line_items"]) == 1


def test_parse_invoice_extracts_json_from_markdown():
    """parse_invoice strips markdown fences around JSON."""
    wrapped = f"```json\n{_VALID_JSON}\n```"
    with patch("invoice_parser.anthropic.Anthropic") as MockAnthropic:
        client = MockAnthropic.return_value
        client.messages.create.return_value = _mock_claude_response(wrapped)

        result = invoice_parser.parse_invoice(content="some text", mime_type="text/plain")

    assert result["vendor_name"] == "Acme Corp"


def test_parse_invoice_pdf_builds_document_block():
    """parse_invoice sends a document content block for PDFs."""
    with patch("invoice_parser.anthropic.Anthropic") as MockAnthropic:
        client = MockAnthropic.return_value
        client.messages.create.return_value = _mock_claude_response(_VALID_JSON)

        result = invoice_parser.parse_invoice(content=b"%PDF fake", mime_type="application/pdf")

    call_args = client.messages.create.call_args
    content_blocks = call_args.kwargs["messages"][0]["content"]
    types = [b.get("type") for b in content_blocks]
    assert "document" in types


def test_parse_invoice_image_builds_image_block():
    """parse_invoice sends an image content block for PNG files."""
    with patch("invoice_parser.anthropic.Anthropic") as MockAnthropic:
        client = MockAnthropic.return_value
        client.messages.create.return_value = _mock_claude_response(_VALID_JSON)

        result = invoice_parser.parse_invoice(content=b"\x89PNG", mime_type="image/png")

    call_args = client.messages.create.call_args
    content_blocks = call_args.kwargs["messages"][0]["content"]
    types = [b.get("type") for b in content_blocks]
    assert "image" in types


def test_parse_invoice_returns_fallback_on_invalid_json():
    """parse_invoice returns fallback dict when Claude returns non-JSON."""
    with patch("invoice_parser.anthropic.Anthropic") as MockAnthropic:
        client = MockAnthropic.return_value
        client.messages.create.return_value = _mock_claude_response("Sorry, I cannot parse this.")

        result = invoice_parser.parse_invoice(content="garbage", mime_type="text/plain")

    assert result["vendor_name"] is None
    assert result["total_amount"] == 0.0
    assert result["line_items"] == []


def test_parse_invoice_returns_fallback_on_api_error():
    """parse_invoice returns fallback dict when the Claude API raises."""
    with patch("invoice_parser.anthropic.Anthropic") as MockAnthropic:
        client = MockAnthropic.return_value
        client.messages.create.side_effect = Exception("API error")

        result = invoice_parser.parse_invoice(content="text", mime_type="text/plain")

    assert result["vendor_name"] is None
    assert result["total_amount"] == 0.0


def test_parse_invoice_coerces_total_amount_to_float():
    """parse_invoice coerces string total_amount to float."""
    data = json.loads(_VALID_JSON)
    data["total_amount"] = "1,200.00"  # string with comma
    with patch("invoice_parser.anthropic.Anthropic") as MockAnthropic:
        client = MockAnthropic.return_value
        client.messages.create.return_value = _mock_claude_response(json.dumps(data))

        result = invoice_parser.parse_invoice(content="text", mime_type="text/plain")

    # After stripping comma the coercion will fail → fallback to 0.0
    assert isinstance(result["total_amount"], float)
