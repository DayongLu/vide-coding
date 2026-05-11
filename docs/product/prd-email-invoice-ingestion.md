# PRD: Email Invoice Ingestion

**Status:** Backfilled — feature shipped on branch `feat/email-invoice-ingestion` (commits `fa12743`, `b9ef7f0`)
**Date:** 2026-05-11
**Author:** Product
**Scope:** `src/email_client.py`, `src/gmail_auth.py`, `src/invoice_parser.py`, `src/invoice_scanner.py`, `src/api/routers/invoices.py`, `src/tools.py`

---

## 1. Problem Statement

Even after the Bill Payment work, the agent still cannot create the *bills themselves*. Vendors send invoices by email as PDF or image attachments. To get those bills into QuickBooks today, the AP manager has to:

1. Open the email.
2. Read the PDF/image to extract vendor, line items, amount, and due date.
3. Switch to QBO.
4. Manually type the bill into the Bills form.
5. Match it to the right vendor and expense account.

This is the highest-frequency, highest-friction step in the AP workflow. Every dollar that ends up as a `BillPayment` in QBO started as an invoice in an inbox. If the agent can automate steps 1–4 (with the user only confirming a pre-filled draft), the AP workflow becomes:

> **inbox → review queue → one-click approve → paid bill** — without the human ever transcribing data.

The job-to-be-done:

> **When invoices arrive in my email, I want the agent to extract the relevant fields, match the vendor to my QBO records, and queue them for my review — so that approving a new bill is a single confirmation instead of 5 minutes of typing.**

---

## 2. User Stories

### Story 1 — Scan the inbox for invoices
**As an** AP manager,
**I want to** tell the agent "scan my inbox for new invoices,"
**so that** I do not have to manually open and triage email attachments.

**Acceptance Criteria:**
- The agent fetches unread emails from the Gmail inbox that match invoice heuristics (subject keywords like "invoice", "bill", "statement", "receipt", "remittance", "payment due", OR a PDF/image attachment).
- Processed emails are labelled `InvoiceProcessed` in Gmail so they are not refetched.
- The agent returns a summary: emails scanned, invoices added to queue, invoices skipped (already queued).

---

### Story 2 — Extract structured invoice fields
**As an** AP manager,
**I want the** agent to automatically extract vendor name, invoice number, due date, line items, and total from the attachment,
**so that** I am not retyping fields the LLM can read.

**Acceptance Criteria:**
- PDF and image (PNG/JPEG) attachments are parsed via the configured LLM (default Anthropic Claude, switchable to Gemini or OpenAI via `LLM_PROVIDER` env var).
- TIFF and other unsupported MIME types fall back to a best-effort text prompt.
- If the attachment cannot be parsed (or yields total = 0), the agent falls back to parsing the email body text.
- Extracted fields land in the queue even when partial — the user can correct them at approve time.

---

### Story 3 — Match vendor against QuickBooks
**As an** AP manager,
**I want the** agent to match the parsed vendor name against my QBO vendor list automatically,
**so that** I do not have to look up vendor IDs.

**Acceptance Criteria:**
- After extraction, the agent calls `qbo_client.find_vendor_by_name` to look up the parsed vendor.
- If matched, the queue row stores `vendor_id` and the canonical `DisplayName`.
- If unmatched, `vendor_id` is null and the agent surfaces an "add vendor in QBO first" error at approve time.
- The agent does not auto-create vendors — that is a deliberate guardrail.

---

### Story 4 — Review queue with approve/reject
**As an** AP manager,
**I want to** see the list of pending invoices and either approve (creates a QBO Bill) or reject each one,
**so that** I keep human oversight over what becomes a financial obligation.

**Acceptance Criteria:**
- A SQLite-backed review queue persists invoices across sessions.
- `approve_invoice` with `user_confirmed=False` returns a preview (no QBO write) so the user can verify before committing.
- `approve_invoice` with `user_confirmed=True` calls `qbo_client.create_bill` and updates the queue row to `status='created'` with the QBO `bill_id`.
- `reject_invoice` marks a row `status='rejected'` and stores an optional reason — no QBO write.
- Already-created or already-rejected invoices cannot be re-approved or re-rejected.

---

### Story 5 — Drive the workflow from chat or REST
**As a** frontend developer,
**I want to** drive the same workflow from REST endpoints,
**so that** the future UI does not have to go through the chat agent for every operation.

**Acceptance Criteria:**
- `POST /api/v1/invoices/scan`, `GET /api/v1/invoices/queue`, `POST /api/v1/invoices/{id}/approve`, `POST /api/v1/invoices/{id}/reject` are exposed.
- All four endpoints require API-key auth.
- Endpoint behaviour mirrors the equivalent agent tool exactly (same underlying `invoice_scanner` functions).

---

## 3. RICE Score

| Factor | Estimate | Rationale |
|---|---|---|
| **Reach** | 9 / 10 | Every AP user receives invoices by email. This is the *origin* of the bill-payment workflow already shipped — same population. |
| **Impact** | 9 / 10 | Removes the manual transcription step. Combined with the Bill Payment feature, the agent can now handle the full inbox-to-paid loop. |
| **Confidence** | 6 / 10 | LLM extraction accuracy varies by invoice format. Vendor-name matching is fuzzy. Gmail OAuth + provider rotation adds operational complexity. Backfilled PRD — confidence reflects the production unknowns, not the engineering. |
| **Effort** | 2 sprints (already shipped) | Implementation already complete on branch. Two commits totalling ~2,200 LOC across 14 files. |

**RICE = (9 × 9 × 0.6) / 2 = ~24.3**

---

## 4. Out of Scope

- Auto-creating vendors when no QBO match exists (deliberate guardrail; user must add the vendor in QBO).
- Auto-paying after approval — payment is a separate explicit action handled by the Bill Payment feature.
- Inbox sources other than Gmail (IMAP, Outlook, Exchange, etc.) — Gmail-only for v1.
- Forwarded-email handling and attachment-rewriting workflows.
- Splitting one invoice across multiple expense accounts at approval time.

---

## 5. Success Criteria

| Metric | Target |
|---|---|
| Extraction success rate (PDF + image, vendor + total + due_date all non-null) | ≥ 80% on a held-out set of 20 real invoices |
| Vendor match rate (when vendor exists in QBO) | ≥ 95% |
| End-to-end latency (inbox → queued) for a single email with one PDF | ≤ 10 s |
| False-positive rate (non-invoice emails appearing in the queue) | ≤ 5% |
| Test coverage of new modules | ≥ 85% per module |
