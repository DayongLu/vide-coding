# Finance Agent

An AI-powered accounts payable assistant that connects to QuickBooks Online data. Interfaces: a Flask web chat UI, a standalone MCP server, and a FastAPI backend with SSE streaming.

## Project Structure

```
finance-agent/
├── docs/
│   ├── product/             # PRDs, user stories, requirements (PM owns)
│   └── design/              # UX specs, wireframes, decisions (Design Manager owns)
├── src/
│   ├── api/                 # FastAPI backend (primary interface)
│   │   ├── main.py          # FastAPI app factory + startup
│   │   ├── agent.py         # Async agent loop with SSE streaming
│   │   ├── db.py            # SQLite layer (WAL mode, conversation persistence)
│   │   ├── system_prompt.py # System prompt builder
│   │   ├── logging_config.py # Structured JSON / text logging
│   │   ├── providers/       # LLM provider abstraction layer
│   │   │   ├── base.py      # BaseLLMProvider ABC
│   │   │   ├── anthropic.py # Anthropic Claude implementation
│   │   │   ├── gemini.py    # Google Gemini implementation
│   │   │   └── openai.py    # OpenAI-compatible implementation (works with Ollama)
│   │   └── routers/
│   │       └── conversations.py  # /conversations CRUD + SSE streaming
│   ├── app.py               # Flask web server (/chat, /reset endpoints)
│   ├── chat.py              # Standalone CLI chat interface
│   ├── tools.py             # Single source of truth: TOOLS list + execute_tool()
│   ├── qbo_mcp_server.py    # MCP server exposing 13 QBO tools
│   ├── qbo_client.py        # QuickBooks Online API client
│   ├── qbo_auth.py          # OAuth2 flow for QBO tokens
│   ├── payment_tokens.py    # Thread-safe idempotency token store
│   ├── templates/
│   │   └── index.html       # Single-page chat UI
│   ├── .env                 # Runtime config (gitignored)
│   └── requirements.txt     # Source dependencies
├── tests/
│   ├── test_api_integration.py  # FastAPI endpoint integration tests
│   ├── test_payment_tokens.py
│   ├── test_qbo_client_bill_payment.py
│   ├── test_mcp_bill_payment_tools.py
│   ├── test_tools_parity.py
│   └── requirements.txt     # Test dependencies (pytest, etc.)
├── .claude/agents/          # Agent role definitions
└── CLAUDE.md
```

`src/tools.py` is the single source of truth for all 13 QBO tool definitions and the `execute_tool()` dispatcher. Both `app.py` and `api/agent.py` import from it. The MCP server (`src/qbo_mcp_server.py`) imports `qbo_client` directly.

## Setup

```bash
# Primary venv (FastAPI backend + Flask app + CLI, Python 3.10+)
python3 -m venv .venv
source .venv/bin/activate
pip install -r src/requirements.txt
python3 src/qbo_auth.py               # OAuth flow — opens browser, saves tokens.json

# For MCP server (separate venv)
python3.13 -m venv .venv-mcp
.venv-mcp/bin/pip install mcp python-dotenv requests

# For tests
pip install -r tests/requirements.txt
```

Required `src/.env` variables:

| Variable | Required | Description |
|---|---|---|
| `QBO_CLIENT_ID` | Yes | Intuit OAuth2 client ID |
| `QBO_CLIENT_SECRET` | Yes | Intuit OAuth2 client secret |
| `QBO_REDIRECT_URI` | Yes | OAuth callback URL |
| `QBO_ENVIRONMENT` | Yes | `sandbox` or `production` |
| `ANTHROPIC_API_KEY` | For Anthropic | Claude API key |
| `LLM_PROVIDER` | No | `anthropic` (default), `gemini`, or `openai` |
| `GEMINI_API_KEY` | For Gemini | Google AI API key |
| `OPENAI_API_KEY` | For OpenAI | OpenAI / Ollama API key |
| `OPENAI_BASE_URL` | For Ollama | Base URL override (e.g., `http://localhost:11434/v1`) |
| `OPENAI_MODEL` | For OpenAI | Model name (default: `gpt-4o`) |

## Running

```bash
# FastAPI backend (primary — SSE streaming, conversation history)
source .venv/bin/activate
uvicorn api.main:app --reload --app-dir src

# Flask web UI (legacy)
python3 src/app.py                                    # http://localhost:5001

# CLI chat
python3 src/chat.py

# MCP server
.venv-mcp/bin/python3.13 src/qbo_mcp_server.py        # stdio transport
.venv-mcp/bin/python3.13 src/qbo_mcp_server.py --transport sse  # SSE on port 8080

# Run tests (always use the project venv)
source .venv/bin/activate
python3 -m pytest tests/
```

## LLM Provider Abstraction

The FastAPI agent uses a provider abstraction layer (`src/api/providers/`) so the agent loop is provider-agnostic. The active provider is selected at runtime via the `LLM_PROVIDER` environment variable.

### How it works

`BaseLLMProvider` (ABC in `providers/base.py`) defines a single method:

```python
async def stream_turn(messages, tools, system_prompt) -> AsyncGenerator[dict, None]:
    ...
```

Each provider yields normalized event dicts:

| Event type | Fields | Meaning |
|---|---|---|
| `token` | `text` | Streaming text chunk |
| `tool_start` | `tool` | LLM requested a tool call |
| `done` | `content`, `stop_reason`, `tool_calls` | Turn complete |
| `error` | `error_code`, `message`, `recoverable` | Provider error |

`get_provider()` in `agent.py` reads `LLM_PROVIDER` and returns the correct instance. Unknown values fall back to Anthropic with a warning.

### Adding a new provider

1. Create `src/api/providers/<name>.py` implementing `BaseLLMProvider`.
2. Add a branch in `get_provider()` in `src/api/agent.py`.
3. Add any required SDK to `src/requirements.txt`.
4. Document the new env vars here.

## Conventions

- Default model: `claude-sonnet-4-20250514` (Anthropic provider).
- QBO client functions accept an optional `tokens` parameter (defaults to loading from `tokens.json`).
- `src/tools.py` is the single source of truth for tool definitions — do not duplicate in `app.py`, `chat.py`, or `agent.py`.
- Never commit `src/.env` or `src/tokens.json` (both are gitignored).
- Product documents go in `docs/product/`, design documents go in `docs/design/`.

## Python Best Practices

- Follow PEP 8 style guidelines (naming, spacing, line length).
- Use type hints for function signatures and return types.
- Write docstrings for all public functions and classes (Google style).
- Prefer f-strings over `.format()` or `%` string formatting.
- Use `pathlib.Path` over `os.path` for file path operations.
- Use context managers (`with` statements) for resource handling (files, connections).
- Prefer list/dict comprehensions over manual loops where readability allows.
- Use `logging` module instead of `print()` for operational output.
- Handle exceptions specifically — never use bare `except:`.
- Keep functions focused and short; extract helpers when a function exceeds ~30 lines.

## Team Process Rules

### Plan Before You Build
- **Every role must produce a plan before starting work.** No implementation, test writing, or design work begins without a written plan saved to `docs/`.
- Developers must write an implementation plan with task breakdown before coding.
- Test Lead must write a test plan with test cases before writing test code.
- PM must write a PRD before requesting implementation.
- Design Manager must write a UX spec before handoff to engineering.
- Plans are committed as gate documents before implementation starts.

### Test Reporting Requirements
- **After every test run, the Test Lead must produce a report** saved to `docs/product/reports/`.
- Report must include:
  - **Test coverage report** — lines/branches covered per module (use `pytest --cov=src --cov-report=term-missing`)
  - **Issues found** — table of defects with: ID, severity (blocking/important/minor), description, file/line, status (open/fixed)
  - **Test summary** — total passed/failed/skipped, duration
  - **Verdict** — PASS (all P0/P1 pass, coverage targets met) or FAIL (with blocking items listed)
- Report filename format: `test-report-<feature>-<date>.md`

## Testing Requirements

- **Always run tests before committing.** No code should be committed without passing the test suite.
- Run `python -m pytest` from the project root before every commit.
- New features and bug fixes must include corresponding tests.
- Integration tests live in the `tests/` directory.

## Git Branching and Pull Requests

- **Never push directly to `main`.** All changes must go through a pull request.
- Create a feature branch for every piece of work: `git checkout -b <type>/<short-description>` (e.g., `feat/bill-payment`, `fix/token-expiry`).
- Open a PR with `gh pr create` and wait for review before merging.
- `main` must always be in a releasable state — direct pushes bypass review and are not allowed.

## Git Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/) syntax:

```
<type>(<scope>): <short summary>

<optional body>

<optional footer>
```

**Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `ci`, `perf`, `build`

**Examples:**
- `feat(chat): add bill payment approval workflow`
- `fix(qbo-client): handle expired OAuth token refresh`
- `test(integration): add vendor lookup end-to-end tests`
- `refactor(app): extract tool dispatch into shared module`

Rules:
- Subject line: imperative mood, lowercase, no period, max 72 characters.
- Scope: the module or area affected (e.g., `chat`, `qbo-client`, `mcp`, `auth`).
- Body: explain **what** and **why**, not **how**.
