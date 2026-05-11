# Finance Agent - Project Overview & Guidelines

This project is an AI-powered accounts payable assistant that connects Claude to QuickBooks Online (QBO) via the Model Context Protocol (MCP) or a custom FastAPI backend.

## Project Overview

*   **Purpose:** To help users manage bills, vendors, and financials using natural language, with a built-in confirmation flow for bill payments.
*   **Main Technologies:**
    *   **Backend:** Python 3.10+, FastAPI (replacing legacy Flask `app.py`).
    *   **LLM Integration:** Anthropic Claude (via MCP or direct API).
    *   **External Integration:** QuickBooks Online (QBO) REST API.
    *   **Data Persistence:** SQLite (for conversation history).
    *   **Transport:** MCP (stdio/SSE), REST API (FastAPI), SSE for streaming chat.
*   **Architecture:**
    *   **Client Layer:** Claude Desktop, Cursor, or the Finance Agent Web UI.
    *   **API Layer (`src/api/`):** FastAPI backend managing conversations and streaming replies.
    *   **MCP Server Layer (`src/qbo_mcp_server.py`):** Exposes QBO tools to LLM clients.
    *   **QBO Client Layer (`src/qbo_client.py`):** Handles OAuth2 and QBO REST calls.

## LLM Provider Abstraction

The Finance Agent supports multiple LLM providers through a common abstraction layer. This allows switching between Claude, Gemini, and local models (via Ollama) without changing the core agent logic.

### Supported Providers
- **Anthropic (Claude):** Default provider.
- **Gemini:** Google's Generative AI models.
- **OpenAI:** Compatible with OpenAI, Ollama, and vLLM.

### Environment Variables for Providers
Set these in `src/.env` to configure your preferred LLM:

```env
# Provider Selection (anthropic, gemini, openai)
LLM_PROVIDER=anthropic

# Anthropic Config
ANTHROPIC_API_KEY=sk-ant-...

# Gemini Config
GEMINI_API_KEY=your_gemini_api_key

# OpenAI / Ollama Config
OPENAI_API_KEY=ollama          # Or your OpenAI key
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_MODEL=gemma4:latest     # e.g., gpt-4o, gemma2, etc.
```

## Running the Project
... (rest of the content remains the same)

### 2. Installation
```bash
# General dependencies
pip install -r src/requirements.txt

# MCP specific (requires Python 3.10+)
python3.13 -m venv .venv-mcp
.venv-mcp/bin/pip install mcp python-dotenv requests
```

### 3. Authentication
Authenticate with QuickBooks to generate `src/tokens.json`:
```bash
python3 src/qbo_auth.py
```

### 4. Running the Project
*   **FastAPI Backend (Recommended):**
    ```bash
    python3.13 -m uvicorn api.main:app --reload --port 5001 --app-dir src
    ```
*   **MCP Server (stdio):**
    ```bash
    .venv-mcp/bin/python3.13 src/qbo_mcp_server.py
    ```
*   **MCP Server (SSE):**
    ```bash
    .venv-mcp/bin/python3.13 src/qbo_mcp_server.py --transport sse
    ```
*   **Legacy Flask Web UI:**
    ```bash
    python3 src/app.py
    ```

### 5. Testing
```bash
pip install -r tests/requirements.txt
python -m pytest tests/
# With coverage
python -m pytest tests/ --cov=src --cov-report=term-missing
```

## Development Conventions

### Coding Standards
*   **Python:** Follow PEP 8, use type hints, and Google-style docstrings.
*   **Logging:** Use the `logging` module instead of `print()`.
*   **Structure:** Keep functions focused and short (< 30 lines where possible).

### Multi-Agent Process (Mandatory)
This project follows a strict "Plan Before You Build" process:
1.  **PM** writes a PRD in `docs/product/`.
2.  **Design** writes a UX spec in `docs/design/`.
3.  **Tech Lead** writes an implementation plan.
4.  **Test Lead** writes a test plan.
5.  **Implementation** only starts after plans are committed.
6.  **Test Reports** must be generated after every run in `docs/product/reports/`.

### Git Workflow
*   **No Direct Pushes:** Never push to `main`. Use feature branches (`feat/`, `fix/`, etc.).
*   **Pull Requests:** Open a PR via `gh pr create` and wait for review.
*   **Conventional Commits:** Use `type(scope): message` format.
    *   Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`.

### Bill Payment Flow (Safety First)
The write path enforces a two-step confirmation:
1.  `preview_bill_payment`: Validates data and returns a 5-minute idempotency token.
2.  `create_bill_payment`: Consumes the token and executes the payment only after explicit user confirmation.

## Key Files
*   `src/api/main.py`: Entry point for the FastAPI backend.
*   `src/qbo_mcp_server.py`: MCP server implementation.
*   `src/qbo_client.py`: Core QBO API integration.
*   `src/payment_tokens.py`: Idempotency token management.
*   `CLAUDE.md`: Detailed project conventions and team rules.
*   `docs/design/ux-backend-api.md`: REST API contract and SSE protocol.
