# Finance Agent

An AI-powered accounts payable assistant that connects to QuickBooks Online (QBO). Ask natural-language questions about your bills, vendors, and financials — and pay bills with a built-in confirmation flow. Supports multiple LLM backends (Anthropic Claude, Google Gemini, OpenAI/Ollama).

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Client Layer                             │
│                                                                 │
│   Web UI (frontend)    Claude Desktop / Claude Code / Cursor    │
│   or CLI chat                 (MCP clients)                     │
└──────────┬──────────────────────────┬───────────────────────────┘
           │  HTTP + SSE              │  MCP (stdio or SSE)
           ▼                          ▼
┌──────────────────────┐  ┌──────────────────────────────────────┐
│  FastAPI Backend     │  │         MCP Server Layer             │
│  src/api/            │  │       src/qbo_mcp_server.py          │
│                      │  │                                      │
│  /conversations      │  │  13 tools (10 read + 3 write)        │
│  SSE streaming       │  │  stdio or SSE transport              │
│  SQLite history      │  └──────────────────┬───────────────────┘
│                      │                     │
│  ┌───────────────┐   │                     │  Python calls
│  │ Provider      │   │                     │
│  │ Abstraction   │   │                     │
│  │  ─────────    │   │                     │
│  │  Anthropic    │   │                     │
│  │  Gemini       │   │                     │
│  │  OpenAI       │   │                     │
│  └───────────────┘   │                     │
└──────────┬───────────┘                     │
           │  Python calls                   │
           └──────────────┬──────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    QBO Client Layer                             │
│                   src/qbo_client.py                             │
│                                                                 │
│  OAuth2 tokens (tokens.json)                                    │
│  REST calls → QuickBooks Online API (sandbox or production)     │
└─────────────────────────────────────────────────────────────────┘
```

### Key design decisions

- **Provider abstraction** — `src/api/providers/` defines a `BaseLLMProvider` ABC so the agent loop is provider-agnostic. Switch providers at runtime with `LLM_PROVIDER=gemini|openai|anthropic`.
- **MCP over direct API** — The MCP server exposes QBO as tools so any MCP-compatible LLM client can use it without embedding API logic in prompts.
- **Two-step payment flow** — Bill payments require `preview_bill_payment` (validate + mint token) followed by `create_bill_payment` (consume token + post to QBO). This prevents accidental payments and gives users an explicit confirmation step.
- **Idempotency tokens** — `payment_tokens.py` issues single-use, 5-minute TTL tokens. The execution step cannot proceed without a valid token, and each token can only be used once.
- **Structured error envelopes** — Every tool returns a consistent `{status, error_code, message, recoverable}` JSON envelope on failure, so the LLM can decide whether to retry or surface the error to the user.
- **Dual transport** — The MCP server supports `stdio` (for Claude Code / Claude Desktop) and `SSE` (for web-based clients on port 8080).
- **SSE streaming** — The FastAPI backend streams agent responses token-by-token using Server-Sent Events so the UI updates in real time.

---

## Project Structure

```
finance-agent/
├── src/
│   ├── api/                    # FastAPI backend
│   │   ├── main.py             # App factory, startup, CORS
│   │   ├── agent.py            # Agent loop — provider dispatch, tool execution, SSE
│   │   ├── db.py               # SQLite layer (WAL mode, conversation history)
│   │   ├── system_prompt.py    # System prompt builder
│   │   ├── logging_config.py   # JSON / text structured logging
│   │   ├── providers/
│   │   │   ├── base.py         # BaseLLMProvider ABC
│   │   │   ├── anthropic.py    # Anthropic Claude
│   │   │   ├── gemini.py       # Google Gemini
│   │   │   └── openai.py       # OpenAI / Ollama
│   │   └── routers/
│   │       └── conversations.py  # CRUD + SSE streaming endpoints
│   ├── tools.py                # 13 QBO tool definitions + execute_tool() dispatcher
│   ├── qbo_mcp_server.py       # MCP server — 13 tools exposed to LLM clients
│   ├── qbo_client.py           # QBO REST API client (reads + writes)
│   ├── payment_tokens.py       # Thread-safe idempotency token store
│   ├── qbo_auth.py             # OAuth2 flow — opens browser, saves tokens.json
│   ├── app.py                  # Flask web UI (legacy interface)
│   ├── chat.py                 # CLI chat interface
│   ├── templates/
│   │   └── index.html          # Single-page chat UI
│   ├── .env                    # Runtime config (gitignored)
│   └── requirements.txt
├── tests/
│   ├── test_api_integration.py
│   ├── test_payment_tokens.py
│   ├── test_qbo_client_bill_payment.py
│   ├── test_mcp_bill_payment_tools.py
│   ├── test_tools_parity.py
│   └── requirements.txt
├── docs/
│   ├── product/            # PRDs, plans, test reports
│   └── design/             # UX specs
├── .claude/
│   └── agents/             # Agent role definitions (PM, Design, Tech Lead, Test Lead)
├── CLAUDE.md               # Project conventions and team process rules
└── README.md
```

---

## Setup

### 1. Prerequisites

- Python 3.10+ (FastAPI backend and MCP server both require 3.10+)
- A QuickBooks Online account (sandbox or production)
- An [Intuit Developer](https://developer.intuit.com/) app with OAuth2 credentials
- API key for your chosen LLM provider (Anthropic, Gemini, or OpenAI)

### 2. Environment variables

Create `src/.env`:

```env
# QuickBooks Online (required)
QBO_CLIENT_ID=your_intuit_client_id
QBO_CLIENT_SECRET=your_intuit_client_secret
QBO_REDIRECT_URI=http://localhost:8080/callback
QBO_ENVIRONMENT=sandbox          # or: production

# LLM Provider — pick one
LLM_PROVIDER=anthropic           # default; options: anthropic, gemini, openai

ANTHROPIC_API_KEY=sk-ant-...     # if LLM_PROVIDER=anthropic
GEMINI_API_KEY=...               # if LLM_PROVIDER=gemini
OPENAI_API_KEY=...               # if LLM_PROVIDER=openai
OPENAI_BASE_URL=http://localhost:11434/v1  # for Ollama (optional)
OPENAI_MODEL=gpt-4o              # override model name (optional)

# Logging (optional)
LOG_LEVEL=INFO
LOG_FORMAT=text                  # or: json (for log aggregation pipelines)
```

### 3. Authenticate with QuickBooks

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r src/requirements.txt
python3 src/qbo_auth.py
```

This opens a browser, completes the OAuth2 flow, and writes `src/tokens.json`. Keep this file safe — it is gitignored.

### 4. Install MCP server dependencies (optional)

```bash
python3.13 -m venv .venv-mcp
.venv-mcp/bin/pip install mcp python-dotenv requests
```

---

## Running

### FastAPI backend (primary)

```bash
source .venv/bin/activate
cd src
uvicorn api.main:app --reload
# API available at http://localhost:8000
# Docs at http://localhost:8000/docs
```

### MCP server (for Claude Desktop / Claude Code / Cursor)

```bash
# stdio transport
.venv-mcp/bin/python3.13 src/qbo_mcp_server.py

# SSE transport — for web-based MCP clients (port 8080)
.venv-mcp/bin/python3.13 src/qbo_mcp_server.py --transport sse
```

### Flask web UI (legacy)

```bash
python3 src/app.py
# Open http://localhost:5001
```

### CLI chat

```bash
python3 src/chat.py
```

---

## LLM Provider Support

The FastAPI backend uses a provider abstraction layer so you can switch LLM backends without changing application code.

| Provider | `LLM_PROVIDER` value | Required env var | Notes |
|---|---|---|---|
| Anthropic Claude | `anthropic` (default) | `ANTHROPIC_API_KEY` | Uses `claude-sonnet-4-20250514` |
| Google Gemini | `gemini` | `GEMINI_API_KEY` | Uses `gemini-2.0-flash` |
| OpenAI | `openai` | `OPENAI_API_KEY` | Uses `gpt-4o` by default |
| Ollama (local) | `openai` | `OPENAI_API_KEY=ollama` | Set `OPENAI_BASE_URL=http://localhost:11434/v1` and `OPENAI_MODEL=llama3` |

All providers implement the same normalized event stream (`token`, `tool_start`, `done`, `error`) defined in `src/api/providers/base.py`.

---

## Claude Code Workflow

This project is built and maintained using Claude Code with a multi-agent team process.

### Agent roles

| Role | Responsibility | Docs produced |
|---|---|---|
| Product Manager | PRDs, user stories, requirements | `docs/product/prd-*.md` |
| Design Manager | UX specs, interaction flows | `docs/design/ux-*.md` |
| Technical Lead | Architecture, code review | Implementation plans |
| Test Lead | Test plans, reports, coverage | `docs/product/test-plan-*.md`, `docs/product/reports/` |

### Process

Every feature follows this pipeline:

```
PM writes PRD → Design writes UX spec → Dev writes implementation plan
→ Implementation → Tests written & run → Tech Lead review → PR → merge
```

No role starts work without a written plan committed to `docs/` first.

### Git workflow

```bash
# 1. Create a feature branch
git checkout -b feat/your-feature-name

# 2. Make changes, run tests before every commit
python -m pytest tests/

# 3. Commit using Conventional Commits
git commit -m "feat(scope): short description"

# 4. Push and open a PR — never push directly to main
git push origin feat/your-feature-name
gh pr create
```

**Never push directly to `main`.** All changes go through a pull request.

### Commit message format

```
<type>(<scope>): <short summary>
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`

Examples:
- `feat(mcp): add get_bill_by_id tool`
- `fix(qbo-client): handle expired OAuth token on refresh`
- `test(payments): add duplicate detection coverage`

---

## Bill Payment Flow

The write path enforces a mandatory two-step confirmation flow:

```
User: "Pay bill #123"
         │
         ▼
  preview_bill_payment(bill_id, account_id, amount?)
         │
         ├─ Validates bill exists and has a balance
         ├─ Validates payment account is a Bank account
         ├─ Validates amount ≤ balance
         ├─ Validates payment date within [-90, +30] days
         ├─ Warns if account balance < payment amount
         └─ Returns preview + confirmation_token (5-min TTL)
         │
         ▼
  Claude presents preview to user and asks for confirmation
         │
         ▼
  create_bill_payment(confirmation_token, user_confirmed=True)
         │
         ├─ Validates token (not expired, not used)
         ├─ Checks for duplicate payment in last 24h
         └─ POSTs BillPayment to QuickBooks Online
```

---

## Testing

```bash
pip install -r tests/requirements.txt

# Run all tests
python -m pytest tests/

# Run with coverage report
python -m pytest tests/ --cov=src --cov-report=term-missing
```

After every test run the Test Lead produces a report in `docs/product/reports/test-report-<feature>-<date>.md`.

---

## MCP Tools Reference

| Tool | Type | Description |
|---|---|---|
| `get_company_info` | Read | Company name, address, contact info |
| `get_vendors` | Read | Vendor/supplier list |
| `get_bills` | Read | All bills (accounts payable) |
| `get_unpaid_bills` | Read | Bills with remaining balance > 0 |
| `get_bill_payments` | Read | Historical payment records |
| `get_accounts` | Read | Chart of accounts (filterable by type) |
| `get_invoices` | Read | Invoices sent to customers (AR) |
| `get_customers` | Read | Customer list |
| `get_profit_and_loss` | Read | P&L report |
| `get_balance_sheet` | Read | Balance sheet report |
| `get_bill_by_id` | Read | Single bill lookup by ID |
| `preview_bill_payment` | Write | Validate payment + mint confirmation token |
| `create_bill_payment` | Write | Execute payment using confirmation token |
