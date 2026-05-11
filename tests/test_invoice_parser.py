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


# ---------------------------------------------------------------------------
# Provider routing tests
# ---------------------------------------------------------------------------


def test_call_llm_routes_to_gemini():
    """LLM_PROVIDER=gemini delegates to _call_gemini."""
    with patch("invoice_parser._call_gemini", return_value=_VALID_JSON) as mock_gemini:
        with patch.dict("os.environ", {"LLM_PROVIDER": "gemini"}):
            result = invoice_parser.parse_invoice("some invoice text", "text/plain")

    mock_gemini.assert_called_once_with("some invoice text", "text/plain")
    assert result["vendor_name"] == "Acme Corp"
    assert result["total_amount"] == 1200.0


def test_call_llm_routes_to_openai():
    """LLM_PROVIDER=openai delegates to _call_openai."""
    with patch("invoice_parser._call_openai", return_value=_VALID_JSON) as mock_openai:
        with patch.dict("os.environ", {"LLM_PROVIDER": "openai"}):
            result = invoice_parser.parse_invoice("some invoice text", "text/plain")

    mock_openai.assert_called_once_with("some invoice text", "text/plain")
    assert result["vendor_name"] == "Acme Corp"


def test_parse_invoice_openai_pdf_fallback():
    """OpenAI provider is called with application/pdf mime type for PDFs."""
    with patch("invoice_parser._call_openai", return_value=_VALID_JSON) as mock_openai:
        with patch.dict("os.environ", {"LLM_PROVIDER": "openai"}):
            invoice_parser.parse_invoice(b"%PDF fake", "application/pdf")

    args = mock_openai.call_args[0]
    assert args[1] == "application/pdf"


# ---------------------------------------------------------------------------
# Anthropic content-block builder edge cases
# ---------------------------------------------------------------------------


def test_build_anthropic_content_tiff_falls_back_to_text():
    """TIFF images bypass the image block and send a text-only prompt."""
    blocks = invoice_parser._build_anthropic_content(b"\x49\x49", "image/tiff")
    assert len(blocks) == 1
    assert blocks[0]["type"] == "text"
    assert "TIFF" in blocks[0]["text"]


def test_build_anthropic_content_jpg_normalized_to_jpeg():
    """image/jpg is normalized to image/jpeg as required by the API."""
    blocks = invoice_parser._build_anthropic_content(b"\xff\xd8\xff", "image/jpg")
    image_block = next(b for b in blocks if b["type"] == "image")
    assert image_block["source"]["media_type"] == "image/jpeg"


# ---------------------------------------------------------------------------
# Gemini provider — content routing
# ---------------------------------------------------------------------------


def _gemini_response(text: str):
    resp = MagicMock()
    resp.text = text
    return resp


def test_call_gemini_pdf_sends_inline_part():
    """_call_gemini sends a PDF as an inline mime/data part."""
    fake_model = MagicMock()
    fake_model.generate_content.return_value = _gemini_response(_VALID_JSON)

    with patch.dict("os.environ", {"LLM_PROVIDER": "gemini"}), \
         patch("google.generativeai.GenerativeModel", return_value=fake_model):
        invoice_parser.parse_invoice(b"%PDF fake", "application/pdf")

    args = fake_model.generate_content.call_args[0][0]
    # First arg is a list: [{mime_type, data}, prompt_text]
    assert isinstance(args, list)
    assert args[0]["mime_type"] == "application/pdf"
    assert args[0]["data"] == b"%PDF fake"


def test_call_gemini_jpg_normalized_to_jpeg():
    """_call_gemini normalizes image/jpg → image/jpeg for the Gemini API."""
    fake_model = MagicMock()
    fake_model.generate_content.return_value = _gemini_response(_VALID_JSON)

    with patch.dict("os.environ", {"LLM_PROVIDER": "gemini"}), \
         patch("google.generativeai.GenerativeModel", return_value=fake_model):
        invoice_parser.parse_invoice(b"\xff\xd8\xff", "image/jpg")

    args = fake_model.generate_content.call_args[0][0]
    assert args[0]["mime_type"] == "image/jpeg"


def test_call_gemini_tiff_uses_text_fallback():
    """_call_gemini uses a text-only fallback for TIFF (unsupported by Gemini)."""
    fake_model = MagicMock()
    fake_model.generate_content.return_value = _gemini_response(_VALID_JSON)

    with patch.dict("os.environ", {"LLM_PROVIDER": "gemini"}), \
         patch("google.generativeai.GenerativeModel", return_value=fake_model):
        invoice_parser.parse_invoice(b"II*\x00", "image/tiff")

    arg = fake_model.generate_content.call_args[0][0]
    assert isinstance(arg, str)
    assert "TIFF" in arg


def test_call_gemini_text_input():
    """_call_gemini sends plain text via a single-string prompt."""
    fake_model = MagicMock()
    fake_model.generate_content.return_value = _gemini_response(_VALID_JSON)

    with patch.dict("os.environ", {"LLM_PROVIDER": "gemini"}), \
         patch("google.generativeai.GenerativeModel", return_value=fake_model):
        invoice_parser.parse_invoice("Invoice for $100", "text/plain")

    arg = fake_model.generate_content.call_args[0][0]
    assert isinstance(arg, str)
    assert "Invoice for $100" in arg


# ---------------------------------------------------------------------------
# OpenAI provider — content routing
# ---------------------------------------------------------------------------


def _openai_response(text: str):
    choice = MagicMock()
    choice.message.content = text
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def test_call_openai_image_sends_data_uri():
    """_call_openai sends images as data: URIs in an image_url content block."""
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _openai_response(_VALID_JSON)

    with patch.dict("os.environ", {"LLM_PROVIDER": "openai"}), \
         patch("openai.OpenAI", return_value=fake_client):
        invoice_parser.parse_invoice(b"\x89PNG", "image/png")

    messages = fake_client.chat.completions.create.call_args.kwargs["messages"]
    content_block = messages[0]["content"][0]
    assert content_block["type"] == "image_url"
    assert content_block["image_url"]["url"].startswith("data:image/png;base64,")


def test_call_openai_pdf_uses_text_only_prompt():
    """_call_openai falls back to a text-only prompt for PDFs (no inline upload)."""
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _openai_response(_VALID_JSON)

    with patch.dict("os.environ", {"LLM_PROVIDER": "openai"}), \
         patch("openai.OpenAI", return_value=fake_client):
        invoice_parser.parse_invoice(b"%PDF fake", "application/pdf")

    messages = fake_client.chat.completions.create.call_args.kwargs["messages"]
    assert isinstance(messages[0]["content"], str)
    assert "PDF document" in messages[0]["content"]


def test_call_openai_text_input():
    """_call_openai sends plain text body as a single-string content."""
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _openai_response(_VALID_JSON)

    with patch.dict("os.environ", {"LLM_PROVIDER": "openai"}), \
         patch("openai.OpenAI", return_value=fake_client):
        invoice_parser.parse_invoice("Invoice from Acme", "text/plain")

    messages = fake_client.chat.completions.create.call_args.kwargs["messages"]
    assert "Invoice from Acme" in messages[0]["content"]


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


def test_parse_invoice_handles_non_list_line_items():
    """parse_invoice coerces non-list line_items to an empty list."""
    bad = {
        "vendor_name": "X",
        "invoice_number": "1",
        "invoice_date": "2026-01-01",
        "due_date": "2026-02-01",
        "line_items": "not-a-list",
        "total_amount": 10.0,
        "currency": "USD",
        "raw_text": "",
    }
    with patch("invoice_parser.anthropic.Anthropic") as MockAnthropic:
        client = MockAnthropic.return_value
        client.messages.create.return_value = _mock_claude_response(json.dumps(bad))
        result = invoice_parser.parse_invoice("text", "text/plain")
    assert result["line_items"] == []


def test_parse_invoice_handles_null_total_amount():
    """parse_invoice coerces null total_amount to 0.0 without raising."""
    bad = {
        "vendor_name": "X",
        "invoice_number": None,
        "invoice_date": None,
        "due_date": None,
        "line_items": [],
        "total_amount": None,
        "currency": "USD",
        "raw_text": "",
    }
    with patch("invoice_parser.anthropic.Anthropic") as MockAnthropic:
        client = MockAnthropic.return_value
        client.messages.create.return_value = _mock_claude_response(json.dumps(bad))
        result = invoice_parser.parse_invoice("text", "text/plain")
    assert result["total_amount"] == 0.0
