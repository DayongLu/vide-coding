# PRD: Bill Payment Creation in Finance Agent

**Status:** Draft  
**Date:** 2026-04-12  
**Author:** Product  
**Scope:** `qbo_client.py`, `qbo_mcp_server.py`, `app.py`, `chat.py`

---

## 1. Problem Statement

The Finance Agent today is a read-only tool. Users can ask "which bills are overdue?" and get a clear answer — but when they want to act on that answer, they have to leave the agent, switch to the QuickBooks Online UI, and complete the payment manually.

This context-switch is the core friction. An AP assistant that can surface what needs to be paid but cannot help execute the payment is only halfway useful. The job-to-be-done is:

> **When I'm reviewing my unpaid bills, I want to initiate payment against a specific bill right here, without having to open a second application, so that I can close out my AP queue in one focused session.**

Adding write support for bill payments is the single highest-leverage action to make the agent genuinely action-oriented rather than merely informational.

---

## 2. User Stories

### Story 1 — Pay a single bill by ID
**As an** AP manager,  
**I want to** tell the agent "pay bill #123 from my Chase checking account,"  
**so that** I can settle a specific vendor invoice without navigating to QBO.

**Acceptance Criteria:**
- Given I provide a bill ID and a bank account name or ID, the agent creates a `BillPayment` record in QBO via POST.
- The payment date defaults to today if not specified; the user can override it.
- The payment amount defaults to the full remaining balance; the user can override it (partial payments).
- The agent returns a confirmation that includes: QBO BillPayment ID, vendor name, amount paid, and payment date.
- If the bill ID does not exist or the balance is already $0, the agent returns a clear error message — no payment is created.

---

### Story 2 — Pay multiple bills in one request
**As an** AP manager,  
**I want to** say "pay all overdue bills from Acme Corp from my operating account,"  
**so that** I can clear a vendor's entire balance in a single instruction.

**Acceptance Criteria:**
- The agent calls `get_unpaid_bills` (or `get_bills` filtered by vendor) to identify matching bills before executing any writes.
- The agent presents a summary ("Found 3 unpaid bills totaling $4,200 from Acme Corp. Confirm payment?") and waits for explicit user confirmation before POSTing.
- Each bill generates a separate `BillPayment` record in QBO (QBO does not support multi-bill batch payments in one API call).
- The agent reports success/failure for each bill individually.
- If any single payment fails, the agent reports which ones succeeded and which failed — it does not roll back completed payments.

---

### Story 3 — Confirm before executing writes
**As an** AP manager,  
**I want the** agent to always show me what it is about to do and ask for my OK before writing to QBO,  
**so that** I never accidentally pay the wrong vendor or wrong amount.

**Acceptance Criteria:**
- Every `create_bill_payment` call is preceded by a natural-language confirmation step in the conversation.
- The confirmation message always states: vendor name, bill ID, amount, payment account, and payment date.
- The payment is only executed if the user replies with an affirmative (yes, confirm, ok, proceed, etc.).
- A negative reply (no, cancel, stop) aborts without writing anything to QBO.
- Confirmation logic lives in the agent's system prompt / conversation flow, not in `qbo_client.py` (client stays thin).

---

### Story 4 — Select the payment bank account
**As an** AP manager,  
**I want to** specify which bank account to pay from (by name or partial name),  
**so that** payments post to the correct account in my chart of accounts.

**Acceptance Criteria:**
- The `create_bill_payment` tool accepts a `bank_account_id` parameter.
- If the user supplies a name instead of an ID, the agent calls `get_accounts(account_type='Bank')` first to resolve the name to an ID.
- If the name is ambiguous (matches more than one account), the agent lists the matches and asks the user to clarify.
- If no bank account is specified, the agent asks before proceeding — it never defaults silently.

---

### Story 5 — Audit trail visibility
**As an** AP manager,  
**I want to** ask the agent "what payments did we make today?" right after executing payments,  
**so that** I can verify the work before ending my session.

**Acceptance Criteria:**
- The existing `get_bill_payments` tool continues to work unchanged.
- Newly created payments appear in `get_bill_payments` results immediately (same session, after QBO processes the POST).
- The agent can reference the QBO BillPayment ID returned from creation to look up the specific record on demand.

---

## 3. RICE Score

| Factor | Estimate | Rationale |
|---|---|---|
| **Reach** | 8 / 10 | Every user who actively uses the AP read features is a potential beneficiary. Paying bills is a universal AP task. |
| **Impact** | 9 / 10 | Closes the "inform but cannot act" gap. Transforms the agent from a reporting tool into an AP workflow tool — significant jump in perceived value. |
| **Confidence** | 7 / 10 | QBO BillPayment API is well-documented and stable. Main uncertainty is around error cases (token expiry mid-payment, partial payment semantics) and how much guardrail UX adds implementation complexity. |
| **Effort** | 3 sprints | `qbo_client.py` needs a POST helper and `create_bill_payment()`. MCP server, `app.py`, and `chat.py` each need the new tool registered. System prompt needs write-operation guidance. Testing against sandbox is mandatory before production. |

**RICE Score = (Reach x Impact x Confidence) / Effort = (8 x 9 x 0.7) / 3 = ~16.8**

This ranks above most read-side enhancements (adding new report types, filtering options) and is the natural next step after the initial read-only POC.

---

## 4. Out of Scope

The following are explicitly NOT part of this feature:

- **Creating new bills** — we are only paying existing bills, not creating new AP records.
- **Voiding or deleting payments** — no destructive write operations in this phase.
- **Approvals workflow** — no multi-step human approvals, roles, or permission levels. A single user confirm in chat is sufficient for now.
- **Recurring / scheduled payments** — one-time payments only. No cron, no standing orders.
- **Check printing** — QBO supports check-type BillPayments; we will only implement `CREDITCARD` and `CHECK` payment types via bank account, not physical check formatting.
- **Foreign currency** — payments will use the company's home currency only.
- **Syncing payment status back from bank** — we write to QBO; we do not integrate with any external bank or payment rail (ACH, wire, etc.).
- **Modifying existing payments** — no update/patch operations on BillPayment objects.
- **Invoice payments (AR side)** — this PRD covers accounts payable only. Receiving customer payments is a separate workstream.

---

## 5. Success Metrics

### Primary (outcome)
| Metric | Target | Measurement |
|---|---|---|
| Payment completion rate | >= 80% of payment intents result in a successful QBO BillPayment record | Log tool call attempts vs. QBO confirmed IDs |
| Error rate | < 5% of payment attempts return an unhandled exception | Exception monitoring on `create_bill_payment` |

### Secondary (adoption)
| Metric | Target | Measurement |
|---|---|---|
| Feature adoption | >= 50% of active weekly users attempt at least one payment via the agent within 30 days of launch | Session logs, tool call tracking |
| Confirmation abort rate | Track what % of confirmations are cancelled | Helps calibrate whether amounts/details in the confirmation prompt are confusing |

### Guardrail (safety)
| Metric | Alert Threshold | Action |
|---|---|---|
| Duplicate payments | 0 tolerance — any report of a double-payment triggers immediate incident review | Compare BillPayment records against bill IDs in session logs |
| Wrong-account payments | 0 tolerance | Post-payment audit: verify `BankAccountRef` in QBO matches what user confirmed |

### Leading indicator
- User messages containing payment intent language ("pay", "settle", "send payment") that were previously dead-ends should decline as users shift to the tool rather than asking and then doing it manually.
