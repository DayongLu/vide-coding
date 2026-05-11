# Implementation Plan: Email Invoice Ingestion

**Status:** Backfilled — feature shipped on branch `feat/email-invoice-ingestion`
**Date:** 2026-05-11
**Author:** Tech Lead
**Scope:** `src/email_client.py`, `src/gmail_auth.py`, `src/invoice_parser.py`, `src/invoice_scanner.py`, `src/api/routers/invoices.py`, `src/tools.py`, `src/api/db.py` (schema)
**Based on:** PRD `prd-email-invoice-ingestion.md` (2026-05-11), UX Spec `ux-email-invoice-ingestion.md` (2026-05-11)

---

## 1. Architecture Overview

The feature is a four-stage pipeline. Each stage is its own module so that a stage can be swapped (e.g., a different LLM provider or a different mail source) without rewriting the others.

```
                ┌────────────────┐
   Gmail        │ email_client   │  fetch_invoice_emails, get_attachments,
   inbox  ────► │  (Google API)  │  get_email_body, mark_as_processed
                └───────┬────────┘
                        │ bytes / text
                        ▼
                ┌────────────────┐
                │ invoice_parser │  parse_invoice → {vendor, total, due_date, …}
                │ (LLM provider) │  Provider selected via LLM_PROVIDER env var
                └───────┬────────┘
                        │ extracted dict
                        ▼
                ┌────────────────┐
                │ invoice_scanner│  scan_emails_for_invoices, get_invoice_queue,
                │   (orchestr.)  │  approve_invoice, reject_invoice
                └───────┬────────┘
                        │ sqlite rows + qbo_client.create_bill
                        ▼
                ┌────────────────┐
                │ QBO Bills      │
                └────────────────┘

Surfaces:
  • Agent tools (tools.py)  ─── scan_email_invoices, list_invoice_queue,
                                approve_email_invoice, reject_email_invoice
  • REST endpoints (api/routers/invoices.py)
        POST /invoices/scan
        GET  /invoices/queue
        POST /invoices/{id}/approve
        POST /invoices/{id}/reject
```

---

## 2. Task Breakdown

### Task 1 — Gmail OAuth + token loader (`src/gmail_auth.py`)

**What:** OAuth2 installed-app flow. `authenticate()` runs `flow.run_local_server(port=0)` and writes tokens JSON; `load_credentials()` rehydrates `Credentials` from the JSON file.

**Scopes:** `gmail.readonly` and `gmail.modify` (modify is needed to add the `InvoiceProcessed` label).

**Paths:** `GMAIL_CREDENTIALS_FILE` and `GMAIL_TOKEN_FILE` env vars override defaults (`src/gmail_credentials.json` and `src/gmail_tokens.json`, both gitignored).

**Done criteria:**
- `authenticate` raises a clear `FileNotFoundError` when the OAuth client file is missing.
- `load_credentials` raises `FileNotFoundError` when the token file is missing (signal to run the auth script).
- Scopes default to module constant when absent from the token file (back-compat).

**Complexity:** S

---

### Task 2 — Gmail API wrapper (`src/email_client.py`)

**What:** Thin wrapper over `googleapiclient.discovery.build("gmail", "v1")`. Exposes four public functions: `fetch_invoice_emails`, `get_attachments`, `get_email_body`, `mark_as_processed`.

**Heuristics for `_looks_like_invoice`:**
- Subject regex: `\b(invoice|bill|statement|receipt|remittance|payment due)\b` (case-insensitive).
- Attachment MIME types accepted: `application/pdf`, `image/png`, `image/jpeg/jpg`, `image/tiff`.

**State tracking:**
- A Gmail label `InvoiceProcessed` is created lazily (`_get_or_create_label`).
- `mark_as_processed` adds the label and removes `UNREAD` so a row never reprocesses.
- `fetch_invoice_emails` query: `label:INBOX is:unread -label:InvoiceProcessed`.

**Body extraction:**
- `_extract_body_text` walks the MIME tree. Prefers `text/plain`; falls back to `text/html` with tags stripped.

**Done criteria:**
- All four public functions covered by tests against mocked Gmail Resource objects.
- Token-refresh path persists refreshed credentials back to disk so the next call does not need re-auth.

**Complexity:** M

---

### Task 3 — Provider-agnostic invoice parser (`src/invoice_parser.py`)

**What:** Single entry point `parse_invoice(content, mime_type, source_email="")` returns a normalised dict with fields: `vendor_name`, `invoice_number`, `invoice_date`, `due_date`, `line_items`, `total_amount`, `currency`, `raw_text`.

**Provider routing:** `LLM_PROVIDER` env var selects one of three implementations.

| `LLM_PROVIDER` | Library | Model env var | Default model |
|---|---|---|---|
| `anthropic` (default) | `anthropic` | `INVOICE_PARSER_MODEL` | `claude-sonnet-4-20250514` |
| `gemini` | `google.generativeai` | `INVOICE_PARSER_MODEL` | `gemini-2.0-flash` |
| `openai` | `openai` | `INVOICE_PARSER_MODEL` | `gpt-4o` |

**Per-provider content rules:**
- **Anthropic:** PDFs → `document` block; PNG/JPEG → `image` block (image/jpg normalised to image/jpeg); TIFF → text-only fallback prompt.
- **Gemini:** PDFs and images → inline `{mime_type, data}` part; TIFF → text-only fallback; text → string prompt.
- **OpenAI:** Images → data URI in `image_url` content block; PDFs → text-only fallback (chat completions does not accept inline PDFs); text → string content.

**Resilience:**
- LLM exceptions caught and the call returns the `_FALLBACK_RESULT` dict.
- LLM responses are searched for a JSON object with regex `\{.*\}` (handles markdown fences).
- Invalid JSON also returns `_FALLBACK_RESULT`.
- `total_amount` is coerced to float (null → 0.0, malformed → 0.0).
- `line_items` is coerced to a list (non-list → empty list).

**Done criteria:**
- Each provider has its own helper function (`_call_anthropic`, `_call_gemini`, `_call_openai`) so each can be tested in isolation.
- All three providers covered by tests with mocked clients.
- Provider routing is decided at every call (no module-level binding) so per-test env-var overrides work cleanly.

**Complexity:** M

---

### Task 4 — Schema additions (`src/api/db.py`)

**What:** Add the `email_invoices` table to the DDL plus two indexes.

```sql
CREATE TABLE IF NOT EXISTS email_invoices (
    id              TEXT PRIMARY KEY,
    email_id        TEXT NOT NULL,
    subject         TEXT,
    from_address    TEXT,
    received_at     TEXT,
    attachment_name TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    extracted_data  TEXT,                     -- JSON blob
    vendor_id       TEXT,
    vendor_name     TEXT,
    bill_id         TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX idx_email_invoices_status ON email_invoices(status, created_at);
CREATE UNIQUE INDEX idx_email_invoices_email_id ON email_invoices(email_id);
```

**Done criteria:**
- Schema applied in `init_db` via `executescript` (idempotent — uses `CREATE TABLE IF NOT EXISTS`).
- Unique index on `email_id` enforces "one row per Gmail message" so duplicate scans skip cleanly.
- Status values are enforced by application logic (no SQL CHECK constraint — keeps migrations easy).

**Complexity:** S

---

### Task 5 — Orchestration layer (`src/invoice_scanner.py`)

**What:** Public functions wired by the tool dispatcher and the FastAPI router.

| Function | Behaviour |
|---|---|
| `scan_emails_for_invoices(max_emails=20)` | Fetch → for each candidate, parse first parseable attachment (or fall back to body), match vendor in QBO, write a `pending` row, mark email processed. Returns `{emails_scanned, invoices_found, invoices_added, invoices_skipped, items}`. |
| `get_invoice_queue(status=None)` | Reads rows, optionally filtered, deserialises `extracted_data`, returns list of summary dicts. |
| `approve_invoice(invoice_queue_id, expense_account_id, user_confirmed)` | If `user_confirmed=False`, return preview only. If True, call `qbo_client.create_bill`, update row to `status='created'` with the `bill_id`. Errors on missing vendor, already-created, already-rejected, unknown id. |
| `reject_invoice(invoice_queue_id, reason="")` | Update row to `status='rejected'`, store reason in JSON. Errors on already-created. |

**Vendor matching:** call `qbo_client.find_vendor_by_name`. `vendor_id` is None when no match — the agent surfaces a "add vendor in QBO first" error at approve time.

**Body fallback:** if no attachment, or if every attachment yields `total_amount == 0`, parse the plain-text body.

**Idempotency:** unique index on `email_id` plus an explicit `SELECT` before insert → duplicate emails are skipped (not errored).

**Done criteria:**
- All four public functions covered by tests with mocked `email_client`, `invoice_parser`, and `qbo_client`.
- End-to-end test exercises the happy path: fetch → parse → vendor-match → insert → mark processed.
- Edge cases tested: duplicate email, no attachment, unmatched vendor, body fallback.

**Complexity:** M

---

### Task 6 — REST endpoints (`src/api/routers/invoices.py`)

**What:** FastAPI router with the four endpoints. All require the `verify_api_key` dependency.

| Endpoint | Body / params | Returns |
|---|---|---|
| `POST /invoices/scan` | `{max_emails: int = 20}` | scanner summary dict |
| `GET /invoices/queue` | `?status=pending\|created\|rejected\|approved` | list of queue rows |
| `POST /invoices/{id}/approve` | `{expense_account_id: str, user_confirmed: bool}` | preview or created-bill dict |
| `POST /invoices/{id}/reject` | `{reason: str = ""}` | confirmation dict |

**Error mapping:**
- `FileNotFoundError` from scan (missing Gmail tokens) → 503 with `error_code=GMAIL_NOT_CONFIGURED`, `recoverable=False`.
- Generic scan exception → 500 with `error_code=SCAN_ERROR`, `recoverable=True`.
- Scanner-returned `{"error": "...not found..."}` → 404; other scanner errors → 400.

**Done criteria:**
- All four endpoints covered by `TestClient` integration tests with mocked `invoice_scanner`.
- Endpoints registered in `create_app()` so `/api/v1/invoices/*` is exposed.

**Complexity:** S

---

### Task 7 — Agent tool registrations (`src/tools.py`)

**What:** Add four tool definitions to the shared TOOLS list and four matching dispatch branches.

| Tool name | Input schema | Calls |
|---|---|---|
| `scan_email_invoices` | `{max_emails: integer = 20}` | `invoice_scanner.scan_emails_for_invoices` |
| `list_invoice_queue` | `{status?: string}` | `invoice_scanner.get_invoice_queue` |
| `approve_email_invoice` | `{invoice_queue_id, expense_account_id, user_confirmed}` | `invoice_scanner.approve_invoice` |
| `reject_email_invoice` | `{invoice_queue_id, reason?}` | `invoice_scanner.reject_invoice` |

**Done criteria:**
- Parity test (`test_tools_parity.py`) still passes — same tool list across all surfaces.
- Tool descriptions emphasise the human-in-loop: preview first, then approve.

**Complexity:** S

---

## 3. Decisions and Trade-offs

| Decision | Chosen | Alternative considered | Why |
|---|---|---|---|
| **Mail provider** | Gmail-only (v1) | IMAP / Outlook / Exchange | Gmail has the cleanest OAuth + JSON API. IMAP would be more portable but adds parsing overhead and credential management. Defer multi-provider until v2. |
| **LLM abstraction** | Provider-agnostic via `LLM_PROVIDER` env var | Lock to Anthropic | Lets us test cost/accuracy trade-offs across providers without code changes. The cost of one extra abstraction layer (~30 LOC of routing) is small. |
| **Vendor auto-create** | No — surface error and require user action | Auto-create with confidence threshold | A wrong vendor in QBO is harder to clean up than a missed bill. Keeps the guardrail explicit. |
| **Storage** | Reuse existing SQLite | Separate invoice DB / queue service | The agent already owns one SQLite. Adding a table is simpler than introducing a queue infra. |
| **Idempotency** | Unique index on `email_id` + explicit SELECT pre-insert | Upsert with `ON CONFLICT` | The explicit check lets us increment `invoices_skipped` cleanly for the response shape. |
| **Reject side-effects** | No QBO write — only DB status update | Send a rejection email back to sender | v1 is internal only — no outbound mail. |

---

## 4. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| LLM extraction is noisy on non-standard invoice layouts | Always require `user_confirmed=True` to actually create a bill — preview shows the parsed fields so the user can spot bad extractions before they hit QBO. |
| Vendor-name fuzzy match returns wrong vendor | `find_vendor_by_name` is an exact (case-insensitive) match against `DisplayName` — no fuzzy logic. Mismatches surface as "no vendor" rather than a wrong-vendor write. |
| Gmail OAuth token expiry mid-run | `_get_service` refreshes expired tokens before each Gmail call and persists the new token. |
| LLM provider outage | The fallback dict is returned on any provider exception; row still lands in the queue with zero amount; user can edit/reject. |
| Cost of LLM calls per scan | `max_emails` parameter caps per-run spend; scan is user-triggered, not on a cron. |

---

## 5. Out of Scope (documented for v2)

- Auto-creating vendors when no QBO match exists.
- Auto-payment after approval — payment is handled by the existing Bill Payment feature, kept as a separate explicit step.
- Recurring invoice detection / deduplication beyond `email_id`.
- Multi-attachment merging into a single bill.
- Splitting one invoice across multiple expense accounts at approval time.
- Mail sources other than Gmail.
