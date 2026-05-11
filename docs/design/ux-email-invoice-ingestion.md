# UX Specification: Email Invoice Ingestion

**Feature:** Pull invoice emails from Gmail, extract fields with an LLM, queue for human review, then create a QBO Bill on approval.
**Context:** Conversational LLM agent (Flask chat UI, CLI chat, MCP server) plus a REST surface for the future frontend.
**Date:** 2026-05-11
**Status:** Backfilled — documents the as-shipped behaviour on branch `feat/email-invoice-ingestion`.

---

## 1. User Flow

The interaction is conversational. The agent owns a three-step flow: **scan → review → approve/reject**. Every QBO write is gated on an explicit user confirmation.

```
User: "Check my inbox for new invoices"
  │
  ├─► [1] SCAN — agent calls scan_email_invoices()
  │         Returns: summary { emails_scanned, invoices_added, invoices_skipped, items }
  │         Agent reports counts and the queued items in plain language.
  │
  ├─► [2] REVIEW — user inspects, or agent calls list_invoice_queue()
  │         Agent surfaces parsed fields per invoice: vendor, total, due date, line items.
  │         User chooses what to approve / reject.
  │
  ├─► [3a] PREVIEW — agent calls approve_email_invoice(user_confirmed=false)
  │           Returns: pre-flight preview (no QBO write).
  │           Agent shows: "About to create a QBO Bill for $X to Vendor Y, due Z. Confirm?"
  │
  ├─► [3b] APPROVE — on "yes": agent calls approve_email_invoice(user_confirmed=true)
  │           Returns: { status: 'created', bill_id, … }
  │           Queue row transitions pending → created.
  │
  └─► [3c] REJECT — on "no" or "skip": agent calls reject_email_invoice()
              Queue row transitions pending → rejected.
              No QBO write.
```

**Key principle:** The agent must always show parsed fields before approving. The two-call shape (`user_confirmed=false` → preview, `user_confirmed=true` → write) makes this a structural requirement of the tool, not a politeness the LLM is asked to obey.

---

## 2. Tool Interface Design

### 2a. `scan_email_invoices`

**Purpose:** Fetch unread invoice-like emails, parse the first one's attachment or body, match the vendor, write a queue row. Idempotent — already-seen emails are skipped.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `max_emails` | int | no | Cap on emails processed per run. Default 20. |

**Returns:**

```json
{
  "emails_scanned": 4,
  "invoices_found": 3,
  "invoices_added": 2,
  "invoices_skipped": 1,
  "items": [
    {
      "id": "<uuid>",
      "subject": "Invoice INV-001 from Acme Corp",
      "from": "billing@acme.com",
      "vendor_name": "Acme Corp",
      "total_amount": 1200.00,
      "due_date": "2026-04-30",
      "vendor_matched": true,
      "status": "pending"
    }
  ]
}
```

**Error envelopes:**

| HTTP / agent-surfaced code | Trigger | recoverable |
|---|---|---|
| `GMAIL_NOT_CONFIGURED` (503) | `gmail_tokens.json` not found | false |
| `SCAN_ERROR` (500) | unexpected exception during scan | true |

---

### 2b. `list_invoice_queue`

**Purpose:** Read the current queue. Read-only. No QBO calls.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `status` | string | no | Filter — `pending` / `created` / `rejected`. Default: all. |

**Returns:** list of queue rows with deserialised extracted fields.

```json
[
  {
    "id": "<uuid>",
    "subject": "Invoice INV-001 from Acme Corp",
    "from_address": "billing@acme.com",
    "received_at": "Mon, 1 Apr 2026 10:00:00 -0700",
    "attachment_name": "INV-001.pdf",
    "status": "pending",
    "vendor_name": "Acme Corp",
    "vendor_id": "10",
    "vendor_matched": true,
    "invoice_number": "INV-001",
    "invoice_date": "2026-04-01",
    "due_date": "2026-04-30",
    "total_amount": 1200.00,
    "currency": "USD",
    "line_items": [{"description": "Consulting", "amount": 1200.00}],
    "bill_id": null,
    "created_at": "2026-05-11T14:32:00+00:00"
  }
]
```

---

### 2c. `approve_email_invoice`

**Purpose:** Preview (no write) or commit (creates a QBO Bill). Two-phase by design.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `invoice_queue_id` | string | yes | Row ID returned by `scan` or `list_invoice_queue`. |
| `expense_account_id` | string | yes | QBO Account ID to book the expense to. |
| `user_confirmed` | bool | yes | `false` → preview only. `true` → create the bill. |

**Returns (preview, `user_confirmed=false`):**

```json
{
  "status": "preview",
  "invoice_queue_id": "<uuid>",
  "vendor_name": "Acme Corp",
  "vendor_id": "10",
  "invoice_number": "INV-001",
  "invoice_date": "2026-04-01",
  "due_date": "2026-04-30",
  "line_items": [{"description": "Consulting", "amount": 1200.00}],
  "total_amount": 1200.00,
  "currency": "USD",
  "expense_account_id": "99",
  "message": "Review the details above and call approve_invoice again with user_confirmed=true to create the bill in QuickBooks."
}
```

**Returns (commit, `user_confirmed=true`):**

```json
{
  "status": "created",
  "bill_id": "412",
  "vendor_name": "Acme Corp",
  "total_amount": 1200.00,
  "due_date": "2026-04-30",
  "message": "Bill #412 created in QuickBooks for Acme Corp."
}
```

**Error envelopes (returned as `{"error": "..."}` and surfaced as 400/404 over REST):**

| Error case | Action required |
|---|---|
| Queue ID not found | 404 — user picked a bad id |
| Already created (idempotency) | Surface the existing `bill_id` |
| Already rejected | User must un-reject (not implemented in v1) or re-scan |
| Vendor not in QBO | Returned with `action_required: "add_vendor"` — agent instructs user to add the vendor in QBO first |

---

### 2d. `reject_email_invoice`

**Purpose:** Mark a queue row as rejected so it never becomes a QBO Bill. Stores an optional reason for audit.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `invoice_queue_id` | string | yes | Row ID. |
| `reason` | string | no | Free-text rejection reason — stored in `extracted_data` JSON. |

**Returns:** `{ status: "rejected", invoice_queue_id, message }`.

**Errors:** 400 if the row was already converted to a QBO Bill (no way to un-create a bill from the queue layer — user must void the bill in QBO directly).

---

## 3. Agent Conversation Patterns

The system prompt teaches the agent to follow these patterns. They are not enforced by the tool layer, but they are the only behaviour we test against.

**Pattern 1 — Always preview before approving:**

> User: "Approve invoice 8a3f for the office supplies account"
> Agent: *[calls approve_email_invoice with user_confirmed=false]*
> Agent: "I'm about to create a QBO Bill for **$420.50** to **Acme Office Supplies**, due **2026-05-30**, charged to **Office Supplies (acct 99)**. Confirm?"
> User: "yes"
> Agent: *[calls approve_email_invoice with user_confirmed=true]*

**Pattern 2 — Surface the queue when asked "what's pending":**

> User: "What invoices do I need to approve?"
> Agent: *[calls list_invoice_queue with status="pending"]*
> Agent: "You have 3 pending invoices: …"

**Pattern 3 — Vendor mismatch:**

> Agent: *[approve returns error with action_required="add_vendor"]*
> Agent: "I can't create this bill yet — **Acme Corp** isn't in your QuickBooks vendor list. Please add the vendor in QBO first, then ask me to approve again."

**Pattern 4 — Honour explicit rejection:**

> User: "Skip the spam one from 'Best Deals'"
> Agent: *[calls reject_email_invoice with reason="Spam — not a real invoice"]*
> Agent: "Marked the 'Best Deals' invoice as rejected. It will not become a QBO Bill."

---

## 4. REST Surface (parallel to agent tools)

The REST endpoints exist so a future frontend can drive the same flow without going through the chat agent. Behaviour mirrors the tools exactly.

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/api/v1/invoices/scan` | `{max_emails?: int}` | Returns scanner summary. |
| GET | `/api/v1/invoices/queue?status=` | — | Returns array of queue rows. |
| POST | `/api/v1/invoices/{id}/approve` | `{expense_account_id, user_confirmed}` | 200 preview or 200 created; 400 / 404 on errors. |
| POST | `/api/v1/invoices/{id}/reject` | `{reason?: string}` | 200 success; 400 if already created. |

All endpoints require the API-key auth header. The scan endpoint maps `FileNotFoundError` to `503 GMAIL_NOT_CONFIGURED` so a frontend can prompt the user to run the OAuth flow.

---

## 5. Accessibility and Trust

The frontend (when built) must:

- **Show parsed fields, not raw OCR.** The whole point of the LLM step is to give the user something they can verify at a glance.
- **Make the source obvious.** Each queue row links back to the original email subject / sender so the user can audit before approving.
- **Mark `vendor_matched=false` clearly.** A red badge or "Add vendor" CTA — not a buried error.
- **Show the resulting QBO Bill ID after approval.** Confirmation that the write succeeded; click-through to QBO when possible.
- **Never collapse preview and approve into one button.** The two-call pattern is a deliberate guardrail and the UI must preserve it.

---

## 6. Telemetry (recommended, not implemented in v1)

To later measure the success criteria in the PRD:

- Log per-scan: `emails_scanned`, `invoices_added`, `invoices_skipped`, duration, LLM provider used.
- Log per-extraction: which fields were null (so we can spot regressions in parser accuracy).
- Log per-approval: time between queue insert and approve (queue dwell time).
- Log vendor-match outcomes (hit / miss with vendor name) — drives the case for fuzzy matching in v2.
