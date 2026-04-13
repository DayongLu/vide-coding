# Finance Agent

An AI-powered accounts payable assistant that connects Claude to QuickBooks Online (QBO) via the Model Context Protocol (MCP). Ask natural-language questions about your bills, vendors, and financials — and pay bills with a built-in confirmation flow.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Client Layer                             │
│                                                                 │
│   Claude Desktop / Claude Code / Cursor / any MCP client        │
│                  or                                             │
│          Flask Web UI  (src/app.py → localhost:5001)            │
└──────────────────────────┬──────────────────────────────────────┘
                           │  MCP (stdio or SSE)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    MCP Server Layer                             │
│                  src/qbo_mcp_server.py                          │
│                                                                 │
│  Read tools (10)              Write tools (3)                   │
│  ─────────────────────        ────────────────────────────────  │
│  get_company_info             get_bill_by_id                    │
│  get_vendors                  preview_bill_payment  ──┐         │
│  get_bills                    create_bill_payment  ◄──┘         │
│  get_unpaid_bills                                               │
│  get_bill_payments        src/payment_tokens.py                 │
│  get_accounts             (idempotency token store)             │
│  get_invoices                                                   │
│  get_customers                                                  │
│  get_profit_and_loss                                            │
│  get_balance_sheet                                              │
└──────────────────────────┬──────────────────────────────────────┘
                           │  Python function calls
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

- **MCP over direct API** — The MCP server exposes QBO as tools so any MCP-compatible LLM client can use it without embedding API logic in prompts.
- **Two-step payment flow** — Bill payments require `preview_bill_payment` (validate + mint token) followed by `create_bill_payment` (consume token + post to QBO). This prevents accidental payments and gives users an explicit confirmation step.
- **Idempotency tokens** — `payment_tokens.py` issues single-use, 5-minute TTL tokens. The execution step cannot proceed without a valid token, and each token can only be used once.
- **Structured error envelopes** — Every tool returns a consistent `{status, error_code, message, recoverable}` JSON envelope on failure, so the LLM can decide whether to retry or surface the error to the user.
- **Dual transport** — The MCP server supports `stdio` (for Claude Code / Claude Desktop) and `SSE` (for web-based clients on port 8080).

---

## Project Structure

```
finance-agent/
├── src/
│   ├── qbo_mcp_server.py   # MCP server — 13 tools exposed to LLM clients
│   ├── qbo_client.py       # QBO REST API client (reads + writes)
│   ├── payment_tokens.py   # Idempotency token store for payment confirmation
│   ├── qbo_auth.py         # OAuth2 flow — opens browser, saves tokens.json
│   ├── app.py              # Flask web UI (alternative to MCP)
│   ├── chat.py             # CLI chat interface (alternative to MCP)
│   ├── templates/
│   │   └── index.html      # Single-page chat UI
│   ├── .env                # Runtime config (gitignored)
│   └── requirements.txt
├── tests/
│   ├── test_payment_tokens.py
│   ├── test_qbo_client_bill_payment.py
│   ├── test_qbo_mcp_server_bill_payment.py
│   ├── conftest.py
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

- Python 3.9+ (for Flask app / CLI)
- Python 3.10+ (for MCP server — required by the `mcp` SDK)
- A QuickBooks Online account (sandbox or production)
- An [Intuit Developer](https://developer.intuit.com/) app with OAuth2 credentials
- An Anthropic API key

### 2. Environment variables

Create `src/.env`:

```env
QBO_CLIENT_ID=your_intuit_client_id
QBO_CLIENT_SECRET=your_intuit_client_secret
QBO_REDIRECT_URI=http://localhost:8080/callback
QBO_ENVIRONMENT=sandbox          # or: production
ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Authenticate with QuickBooks

```bash
pip install -r src/requirements.txt
python3 src/qbo_auth.py
```

This opens a browser, completes the OAuth2 flow, and writes `src/tokens.json`. Keep this file safe — it is gitignored.

### 4. Install MCP server dependencies

```bash
python3.13 -m venv .venv-mcp
.venv-mcp/bin/pip install mcp python-dotenv requests
```

---

## Running

### MCP server (recommended — works with Claude Desktop, Claude Code, Cursor)

```bash
# stdio transport — for Claude Desktop / Claude Code
.venv-mcp/bin/python3.13 src/qbo_mcp_server.py

# SSE transport — for web-based MCP clients (port 8080)
.venv-mcp/bin/python3.13 src/qbo_mcp_server.py --transport sse
```

### Flask web UI

```bash
pip install -r src/requirements.txt
python3 src/app.py
# Open http://localhost:5001
```

### CLI chat

```bash
python3 src/chat.py
```

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
