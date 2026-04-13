# UX Specification: Bill Payment Creation

**Feature:** Write operations to pay bills via the MCP tool interface  
**Context:** MCP server consumed by LLM clients (Claude Code, Claude Desktop, Cursor, etc.)  
**Date:** 2026-04-12  
**Status:** Draft

---

## 1. User Flow

The interaction is conversational. The LLM orchestrates a multi-step flow; tool calls are the mechanism.

```
User: "Pay the Acme Corp bill"
  │
  ├─► [1] LOOKUP — LLM calls get_unpaid_bills() or get_bill_by_id()
  │         Returns: bill details (ID, vendor, amount, due date, balance)
  │
  ├─► [2] PREVIEW — LLM calls preview_bill_payment()
  │         Returns: structured payment preview (see §2)
  │         LLM presents preview to user in plain language
  │
  ├─► [3] CONFIRM — LLM asks user: "Confirm payment of $X to Vendor Y from Account Z?"
  │         User must reply with explicit confirmation (e.g. "yes, confirm")
  │
  └─► [4] EXECUTE — LLM calls create_bill_payment() with confirmed parameters
            Returns: payment record (ID, amount, timestamp, status)
```

**Key principle:** The LLM must never call `create_bill_payment()` without having first presented a preview and received explicit user confirmation. The tool names and docstrings enforce this convention; the spec does not rely on the LLM being trusted to self-govern — the preview step is a required separate tool call, not an optional courtesy.

---

## 2. Tool Interface Design

### 2a. `preview_bill_payment`

**Purpose:** Dry-run validation and preview. No money moves. Required before payment.

**Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `bill_id` | string | yes | QBO Bill entity ID |
| `payment_account_id` | string | yes | Bank/checking account ID to pay from |
| `amount` | number | no | Payment amount in USD. Defaults to full balance if omitted |
| `payment_date` | string | no | ISO 8601 date (YYYY-MM-DD). Defaults to today |
| `memo` | string | no | Optional memo line on the payment |

**Returns (JSON):**

```json
{
  "preview": {
    "bill_id": "123",
    "vendor_name": "Acme Corp",
    "bill_total": 1500.00,
    "bill_balance": 1500.00,
    "payment_amount": 1500.00,
    "payment_date": "2026-04-12",
    "payment_account_name": "Business Checking",
    "payment_account_balance": 42000.00,
    "balance_after_payment": 40500.00,
    "memo": "",
    "is_partial_payment": false
  },
  "validation": {
    "valid": true,
    "warnings": [],
    "errors": []
  },
  "confirmation_token": "prev_abc123_1744684800"
}
```

The `confirmation_token` is a short-lived opaque string (expires after 5 minutes or one use) that `create_bill_payment()` requires. This prevents replay of stale previews.

**Validation checks performed:**
- Bill exists and belongs to this QBO company
- Bill has a remaining balance > 0 (not already paid)
- Payment amount does not exceed bill balance
- Payment account exists, is active, and is a bank/checking type
- Payment account has sufficient funds (warn if balance would go negative)
- Payment date is not more than 90 days in the past or 30 days in the future

---

### 2b. `create_bill_payment`

**Purpose:** Execute the payment. Requires a valid `confirmation_token` from `preview_bill_payment`.

**Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `confirmation_token` | string | yes | Token returned by `preview_bill_payment` |
| `user_confirmed` | boolean | yes | Must be `true`. Explicit signal that user confirmed |

No payment parameters are re-accepted here. All details are encoded in the token. This prevents parameter drift between preview and execution (e.g., a different amount sneaking in).

**Returns (JSON — success):**

```json
{
  "status": "success",
  "payment": {
    "payment_id": "qbo_pay_456",
    "bill_id": "123",
    "vendor_name": "Acme Corp",
    "amount_paid": 1500.00,
    "payment_date": "2026-04-12",
    "payment_account": "Business Checking",
    "remaining_bill_balance": 0.00,
    "memo": "",
    "created_at": "2026-04-12T14:32:00Z"
  },
  "message": "Payment of $1,500.00 to Acme Corp recorded successfully. Bill is now fully paid."
}
```

**Returns (JSON — failure):**

```json
{
  "status": "error",
  "error_code": "TOKEN_EXPIRED",
  "message": "The payment preview has expired. Please run preview_bill_payment again to generate a new confirmation.",
  "recoverable": true
}
```

---

### 2c. `get_bill_by_id`

**Purpose:** Fetch a single bill for lookup before previewing. Supplements the existing `get_unpaid_bills`.

**Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `bill_id` | string | yes | QBO Bill entity ID |

**Returns:** Full bill record (same shape as items in `get_unpaid_bills`).

---

## 3. Interaction Patterns

### Confirmation Requirement

The LLM client must obtain explicit affirmative confirmation from the user before calling `create_bill_payment`. Acceptable confirmation phrases include "yes", "confirm", "proceed", "do it", "go ahead". The LLM should not accept ambiguous responses ("okay", "sure", "I guess") without re-asking.

The `user_confirmed: true` parameter in the tool call documents that confirmation occurred, creating an audit trail in the MCP call log.

### Showing the Payment Preview

When `preview_bill_payment` returns, the LLM should render the preview in plain language, not raw JSON. Suggested format:

```
Payment Preview:
  Vendor:   Acme Corp
  Bill ID:  #123
  Amount:   $1,500.00 (full balance)
  Date:     April 12, 2026
  From:     Business Checking (balance: $42,000.00 → $40,500.00 after payment)
  Memo:     —

Shall I proceed with this payment? Reply "confirm" to execute.
```

### Partial Payments

If the user specifies an amount less than the full balance, the preview must clearly label it as a partial payment and show the remaining balance that will still be owed after payment.

### Ambiguous Bill References

If the user refers to a bill by vendor name and multiple bills exist for that vendor, the LLM must call `get_unpaid_bills`, list the matching bills with amounts and due dates, and ask the user to specify which one before calling `preview_bill_payment`.

---

## 4. Error States

All errors are returned in a consistent envelope:

```json
{
  "status": "error",
  "error_code": "STRING_CONSTANT",
  "message": "Human-readable explanation.",
  "recoverable": true | false,
  "suggested_action": "Optional hint for what to do next."
}
```

| Error Code | Trigger | Recoverable | Suggested Action |
|---|---|---|---|
| `BILL_NOT_FOUND` | `bill_id` does not exist in QBO | Yes | Re-run `get_unpaid_bills` to find valid IDs |
| `BILL_ALREADY_PAID` | Bill balance is $0.00 | No | Show confirmation of prior payment date |
| `BILL_PARTIALLY_APPLIED` | Bill has credits/discounts in flight | Yes | Advise user to resolve in QBO UI first |
| `AMOUNT_EXCEEDS_BALANCE` | `amount` > `bill_balance` | Yes | Suggest using the full balance amount |
| `INVALID_PAYMENT_ACCOUNT` | Account ID not found or wrong type | Yes | Re-run `get_accounts` with type `Bank` |
| `INSUFFICIENT_FUNDS` | Account balance < payment amount | Yes | Show current balance; ask user to confirm or pick another account |
| `PAYMENT_DATE_OUT_OF_RANGE` | Date too far past or future | Yes | Suggest today's date |
| `TOKEN_EXPIRED` | Confirmation token older than 5 min | Yes | Re-run `preview_bill_payment` |
| `TOKEN_ALREADY_USED` | Token was already consumed | No | Re-run `preview_bill_payment` for a new token |
| `USER_NOT_CONFIRMED` | `user_confirmed` is `false` or missing | Yes | Re-present preview and ask for confirmation |
| `QBO_API_ERROR` | Downstream QuickBooks API failure | Maybe | Include QBO error detail; suggest retry |
| `DUPLICATE_PAYMENT` | QBO detects identical payment within 24h | Yes | Warn user, require explicit override acknowledgment |

**Insufficient funds** is treated as a warning in preview (surfaced in `validation.warnings`), not a blocking error, because the account balance in QBO may lag real-world balances. It becomes a hard error only at execution time if QBO rejects it.

---

## 5. Safety Considerations

### Principle: Irreversibility Requires Friction

Payments create accounting records in QuickBooks. Reversing them requires a separate void/refund operation and leaves an audit trail. The design imposes deliberate friction proportional to this risk.

### Safeguards

**Mandatory preview step.** `create_bill_payment` cannot be called without a `confirmation_token` from `preview_bill_payment`. There is no shortcut. This enforces visibility of system status (Nielsen #1) and error prevention (Nielsen #5).

**Token expiry (5 minutes).** A user who previews a payment and walks away cannot have it accidentally executed later. The short TTL ensures the confirmed details are still current.

**Single-use tokens.** Each token works exactly once. Retrying a failed payment requires re-previewing, which shows current balances again.

**`user_confirmed` explicit boolean.** Requiring this field in the API call ensures the LLM cannot call `create_bill_payment` as a side effect of misinterpreting context. It must make an active decision to set this to `true`.

**Duplicate payment detection.** If QBO or the MCP layer detects an identical payment (same bill, same amount) within 24 hours, it raises `DUPLICATE_PAYMENT` and requires the user to acknowledge the duplication before proceeding.

**No bulk payment tool at launch.** Paying multiple bills in a single call is not supported in v1. Each bill requires its own preview-confirm-execute cycle. This limits blast radius from mistakes.

**Dry-run is the default mental model.** The tool naming (`preview_bill_payment` vs `create_bill_payment`) makes the distinction explicit and unsurprising for LLM clients reading the tool list.

### Out of Scope (Intentional Omissions)

- No ability to void or delete payments via MCP (read-only escape hatch; users must go to QBO UI)
- No recurring/scheduled payments
- No multi-bill batch payment

---

## 6. Accessibility of Information

### Payment Result Format

Results must be scannable without parsing JSON. The LLM should always translate the tool response into a structured plain-language summary:

**Success:**
```
Payment recorded.
  Paid:     $1,500.00 to Acme Corp
  Date:     April 12, 2026
  From:     Business Checking
  Bill #123 is now fully paid.
  QBO Payment ID: qbo_pay_456
```

**Partial payment:**
```
Partial payment recorded.
  Paid:            $500.00 to Acme Corp
  Remaining owed:  $1,000.00 on Bill #123
  Date:            April 12, 2026
  From:            Business Checking
  QBO Payment ID:  qbo_pay_457
```

**Error:**
```
Payment could not be completed.
  Reason: The Business Checking account balance ($200.00) is less than the
          payment amount ($1,500.00).
  To resolve: Choose a different payment account or pay a smaller amount.
```

### Field Labeling Conventions

- Amounts: always include currency symbol and two decimal places (`$1,500.00`, not `1500`)
- Dates: always spell out month (`April 12, 2026`, not `2026-04-12`) in LLM-rendered output; ISO 8601 in tool parameters
- IDs: prefix with entity type for clarity (`Bill #123`, `Payment ID: qbo_pay_456`)
- Account names: use the human-readable name from QBO, not the internal ID

### Applying Nielsen's Heuristics

| Heuristic | Application |
|---|---|
| #1 Visibility of system status | Preview always shows before-and-after account balances; success response confirms payment ID and final bill balance |
| #3 User control and freedom | Token expiry gives users time to cancel by doing nothing; no undo tool forces the UI layer to be explicit about irreversibility |
| #5 Error prevention | Mandatory preview + token architecture prevents accidental execution; duplicate detection catches inadvertent double-payments |
| #6 Recognition over recall | Tool returns vendor name, account name, and bill reference — users never need to remember IDs |
| #9 Help users recognize errors | Every error includes a human-readable message and a `suggested_action`; error codes are also machine-readable for LLM handling |
