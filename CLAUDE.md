# Finance Agent

An accounts payable assistant that uses Claude to query QuickBooks Online data. Two interfaces: a Flask web chat UI and a standalone MCP server.

## Project Structure

```
finance-agent/
├── docs/
│   ├── product/             # PRDs, user stories, requirements (PM owns)
│   └── design/              # UX specs, wireframes, decisions (Design Manager owns)
├── src/
│   ├── app.py               # Flask web server (/chat, /reset endpoints)
│   ├── chat.py              # Standalone CLI chat interface
│   ├── qbo_mcp_server.py    # MCP server exposing 10 QBO tools
│   ├── qbo_client.py        # QuickBooks Online API client
│   ├── qbo_auth.py          # OAuth2 flow for QBO tokens
│   ├── templates/
│   │   └── index.html       # Single-page chat UI
│   ├── .env                 # Runtime config (gitignored)
│   └── requirements.txt     # Source dependencies
├── tests/
│   ├── qbo_test.py          # Integration tests
│   └── requirements.txt     # Test dependencies (pytest, etc.)
├── .claude/agents/          # Agent role definitions
└── CLAUDE.md
```

Both `src/app.py` and `src/chat.py` define their own TOOLS list and `execute_tool()` dispatcher. The MCP server (`src/qbo_mcp_server.py`) replaces this pattern by importing `qbo_client` directly.

## Setup

```bash
# For Flask app / CLI chat (Python 3.9+)
pip install -r src/requirements.txt
python3 src/qbo_auth.py               # OAuth flow — opens browser, saves tokens.json

# For MCP server (Python 3.10+ required by mcp SDK)
python3.13 -m venv .venv-mcp
.venv-mcp/bin/pip install mcp python-dotenv requests

# For tests
pip install -r tests/requirements.txt
```

Required `src/.env` variables: `QBO_CLIENT_ID`, `QBO_CLIENT_SECRET`, `QBO_REDIRECT_URI`, `QBO_ENVIRONMENT` (sandbox/production), `ANTHROPIC_API_KEY`.

## Running

```bash
python3 src/app.py                                    # Web UI at http://localhost:5001
python3 src/chat.py                                   # CLI chat
.venv-mcp/bin/python3.13 src/qbo_mcp_server.py        # MCP server (stdio)
.venv-mcp/bin/python3.13 src/qbo_mcp_server.py --transport sse  # MCP server (SSE on port 8080)
python -m pytest tests/                                # Run tests
```

## Conventions

- Use `claude-sonnet-4-20250514` as the model for the agent's API calls.
- QBO client functions accept an optional `tokens` parameter (defaults to loading from `tokens.json`).
- Keep tool definitions in sync between `app.py`, `chat.py`, and `qbo_mcp_server.py`.
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

## Testing Requirements

- **Always run tests before committing.** No code should be committed without passing the test suite.
- Run `python -m pytest` from the project root before every commit.
- New features and bug fixes must include corresponding tests.
- Integration tests live in the `tests/` directory.

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
