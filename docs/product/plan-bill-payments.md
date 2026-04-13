# Implementation Plan: Bill Payment Support (MCP Server)

**Date:** 2026-04-12  
**Author:** Tech Lead  
**Scope:** `src/qbo_client.py`, `src/qbo_mcp_server.py`  
**Based on:** PRD (2026-04-12), UX Spec (2026-04-12)

---

## 1. Task Breakdown

### Task 1 — Add POST support to `qbo_client.py`

**What:** Add a `qbo_post(endpoint, payload, tokens=None)` function alongside the existing `qbo_request()` GET function. It must set `Content-Type: application/json`, serialize the payload, call `response.raise_for_status()`, and return `response.json()`.

**Done criteria:**
- `qbo_post` exists and accepts `endpoint`, `payload`, and optional `tokens`
- Returns parsed JSON on success
- Raises `requests.HTTPError` on 4xx/5xx (callers handle)
- Function signature follows the same `tokens` defaulting pattern as `qbo_request`

**Complexity:** S

---

### Task 2 — Add `get_bill_by_id` to `qbo_client.py`

**What:** Add `get_bill_by_id(bill_id, tokens=None)` that calls `qbo_request(f"bill/{bill_id}", tokens)` and returns the `Bill` object from the response. Raise a clear `ValueError` if the bill is not found (QBO returns a structured error body on 400).

**Done criteria:**
- Returns the full bill dict for a valid ID
- Raises `ValueError("BILL_NOT_FOUND")` when QBO returns a not-found response
- Covered by at least one unit test with a mocked HTTP response

**Complexity:** S

---

### Task 3 — Build the confirmation token module (`src/payment_tokens.py`)

**What:** Create a standalone module responsible for generating, storing, and consuming confirmation tokens. This is pure Python — no QBO calls.

**Implementation details:**
- Token format: `prev_{uuid4_hex[:12]}_{unix_timestamp}` (e.g. `prev_a1b2c3d4e5f6_1744684800`)
- In-memory store: a module-level `dict` mapping token string → `{payload: dict, expires_at: float, used: bool}`
- `generate_token(payment_payload: dict) -> str` — stores payload, sets `expires_at = now + 300`
- `consume_token(token: str) -> dict` — validates existence, expiry, and single-use; marks `used=True`; returns payload. Raises `TokenExpiredError`, `TokenAlreadyUsedError`, or `TokenNotFoundError` (custom exceptions defined in same file).
- TTL and token prefix are module-level constants, easy to change.

**Done criteria:**
- `generate_token` returns a correctly formatted string and stores payload
- `consume_token` returns payload on first call, raises `TokenAlreadyUsedError` on second call with same token
- `consume_token` raises `TokenExpiredError` when called after the TTL
- `consume_token` raises `TokenNotFoundError` for unknown tokens
- Unit tests cover all four cases

**Complexity:** S

---

### Task 4 — Add `preview_bill_payment` to `qbo_client.py`

**What:** Add `preview_bill_payment(bill_id, payment_account_id, amount=None, payment_date=None, memo="", tokens=None)`. This function does validation only — no POST to QBO. It calls existing client functions and returns a structured dict.

**Validation steps (in order):**
1. Fetch bill via `get_bill_by_id` — raises `BILL_NOT_FOUND` if missing
2. Check `Balance > 0` — raise `BILL_ALREADY_PAID` if zero
3. Resolve `payment_account_id` via `get_accounts()` filtered by ID — raise `INVALID_PAYMENT_ACCOUNT` if not found or not a Bank type account
4. Default `amount` to bill `Balance`; validate `amount <= Balance` — raise `AMOUNT_EXCEEDS_BALANCE` if over
5. Default `payment_date` to today (`datetime.date.today().isoformat()`); validate within `[-90, +30]` day window — raise `PAYMENT_DATE_OUT_OF_RANGE` if outside
6. Compare `amount` against account's `CurrentBalance` — add `INSUFFICIENT_FUNDS` to `warnings` if account balance would go negative (not a hard error at preview time)

**Returns:** the full preview dict matching the UX spec §2a shape (without `confirmation_token` — the MCP tool layer adds that after calling `generate_token`).

**Done criteria:**
- Each validation failure raises a domain-specific exception with the error code string as the message
- Happy path returns dict with all required fields: `bill_id`, `vendor_name`, `bill_total`, `bill_balance`, `payment_amount`, `payment_date`, `payment_account_name`, `payment_account_balance`, `balance_after_payment`, `memo`, `is_partial_payment`
- Warnings list is present and empty when no issues found
- Unit tests cover each validation failure path and the happy path

**Complexity:** M

---

### Task 5 — Add `create_bill_payment` to `qbo_client.py`

**What:** Add `create_bill_payment(payment_payload: dict, tokens=None)` that constructs the QBO BillPayment API body and calls `qbo_post`. The `payment_payload` comes from the consumed token — the caller (MCP tool) is responsible for token handling.

**QBO API body shape:**
```json
{
  "PayType": "Check",
  "VendorRef": { "value": "<vendor_id>" },
  "TotalAmt": <amount>,
  "TxnDate": "<payment_date>",
  "CheckPayment": {
    "BankAccountRef": { "value": "<payment_account_id>" }
  },
  "PrivateNote": "<memo>",
  "Line": [
    {
      "Amount": <amount>,
      "LinkedTxn": [{ "TxnId": "<bill_id>", "TxnType": "Bill" }]
    }
  ]
}
```

The function posts to `billpayment` and returns the `BillPayment` object from QBO's response.

**Duplicate detection:** Before posting, query `get_bill_payments` and check for a payment against the same `bill_id` within the past 24 hours. If found, raise `DuplicatePaymentError`. (This is a best-effort check — QBO is authoritative.)

**Done criteria:**
- Constructs a valid QBO BillPayment body from the payload dict
- Calls `qbo_post("billpayment", body, tokens)`
- Returns the created `BillPayment` dict from QBO response
- Raises `DuplicatePaymentError` when a same-bill payment exists within 24 hours
- Raises `QBOAPIError` (wrapping the original HTTPError) on any other QBO failure, preserving the QBO error detail string
- Unit tests cover: happy path (mock POST), duplicate detected, QBO 400 error

**Complexity:** M

---

### Task 6 — Add three MCP tools to `qbo_mcp_server.py`

**What:** Register three new `@mcp.tool()` functions. All error handling converts domain exceptions to the UX spec error envelope JSON.

**6a. `get_bill_by_id(bill_id: str) -> str`**
- Calls `qbo_client.get_bill_by_id(bill_id)`
- On `ValueError`: return error envelope with `BILL_NOT_FOUND`

**6b. `preview_bill_payment(bill_id, payment_account_id, amount=None, payment_date=None, memo="") -> str`**
- Calls `qbo_client.preview_bill_payment(...)` to get validated preview dict
- Calls `payment_tokens.generate_token(preview_dict)` to mint token
- Returns JSON with `{"preview": <preview_dict>, "validation": {"valid": true, "warnings": <list>, "errors": []}, "confirmation_token": <token>}`
- On any domain exception: return error envelope with appropriate error code; do not generate a token

**6c. `create_bill_payment(confirmation_token: str, user_confirmed: bool) -> str`**
- If `user_confirmed is False`: return `USER_NOT_CONFIRMED` error envelope immediately, do not touch token
- Calls `payment_tokens.consume_token(confirmation_token)` — catches `TokenExpiredError`, `TokenAlreadyUsedError`, `TokenNotFoundError` and maps each to the appropriate error code
- Calls `qbo_client.create_bill_payment(payload, tokens=None)`
- On `DuplicatePaymentError`: return `DUPLICATE_PAYMENT` error envelope
- On `QBOAPIError`: return `QBO_API_ERROR` envelope with the upstream detail
- On success: return the success envelope per UX spec §2b

**Done criteria:**
- All three tools appear in `mcp.list_tools()` output
- Each tool returns valid JSON in every branch
- Error envelopes always include `status`, `error_code`, `message`, `recoverable` fields
- Success envelope for `create_bill_payment` includes all fields from UX spec §2b
- Integration test exercises the full preview → confirm → create flow against mocked QBO responses

**Complexity:** M

---

### Task 7 — Update MCP server `instructions` string

**What:** Extend the `FastMCP` constructor's `instructions` parameter to describe the write capability and the mandatory preview-before-payment convention, so LLM clients reading the server manifest understand the required flow.

**Done criteria:**
- Instructions mention that write operations require a preview step before execution
- Instructions note `user_confirmed=true` must reflect genuine user affirmation

**Complexity:** S

---

### Task 8 — Integration test against QBO sandbox

**What:** Add a test script (or pytest module) at `tests/test_bill_payment_integration.py` that runs the full flow against the QBO sandbox using real tokens. Separate from unit tests — requires `tokens.json` and a sandbox bill to exist.

**Done criteria:**
- Script can be run manually: `python tests/test_bill_payment_integration.py`
- Exercises: `get_bill_by_id`, `preview_bill_payment`, then `create_bill_payment` with a real sandbox bill
- Prints the returned QBO BillPayment ID and verifies it appears in `get_bill_payments` within the same run
- Skips gracefully if no `tokens.json` is present

**Complexity:** S

---

## 2. Files to Modify

| File | Changes |
|---|---|
| `src/qbo_client.py` | Add `qbo_post()`, `get_bill_by_id()`, `preview_bill_payment()`, `create_bill_payment()`. Add domain exception classes at top of file (`BillNotFoundError`, `BillAlreadyPaidError`, etc.) or in a separate `src/exceptions.py`. |
| `src/qbo_mcp_server.py` | Add three `@mcp.tool()` functions. Update `instructions` string. Add import of `payment_tokens`. |
| `src/payment_tokens.py` | New file. Token store, `generate_token`, `consume_token`, custom exceptions. |
| `tests/test_bill_payment_unit.py` | New file. Unit tests for Tasks 1–5 using `unittest.mock.patch`. |
| `tests/test_bill_payment_integration.py` | New file. Sandbox integration test for Task 8. |

`app.py` and `chat.py` are out of scope for this plan — the task is scoped to the MCP server only. Keeping them in sync is a follow-on task.

---

## 3. Dependencies Between Tasks

```
Task 1 (qbo_post)
    └─► Task 5 (create_bill_payment in client)

Task 2 (get_bill_by_id in client)
    └─► Task 4 (preview_bill_payment in client)
            └─► Task 6b (preview MCP tool)

Task 3 (payment_tokens module)
    └─► Task 6b (preview MCP tool — generates token)
    └─► Task 6c (create MCP tool — consumes token)

Task 5 (create_bill_payment in client)
    └─► Task 6c (create MCP tool)

Task 6a, 6b, 6c can be implemented together in one sitting once their client-layer dependencies are done.

Task 7 (instructions update) — no blocking dependency; can be done alongside Task 6.

Task 8 (integration test) — depends on Tasks 1–6 being complete.
```

**Recommended sequencing for a single developer:**
1. Task 1 → Task 2 → Task 3 (all small, unblock everything else)
2. Task 4 (preview validation logic, most complex in the client layer)
3. Task 5 (create, depends on Task 1 and domain exceptions from Task 4)
4. Tasks 6a + 6b + 6c + 7 (MCP layer, all client functions now ready)
5. Task 8 (integration test)

---

## 4. Complexity Estimates

| Task | Description | Estimate |
|---|---|---|
| 1 | Add `qbo_post` to client | S |
| 2 | Add `get_bill_by_id` to client | S |
| 3 | Token module | S |
| 4 | `preview_bill_payment` validation logic | M |
| 5 | `create_bill_payment` client function | M |
| 6 | Three MCP tools + error mapping | M |
| 7 | Update MCP instructions string | S |
| 8 | Integration test | S |

**Total estimate:** 3–4 focused half-days for an engineer familiar with the codebase. The PRD's "3 sprints" estimate appears to include `app.py`, `chat.py`, system prompt work, and QA — which is reasonable for the full scope. MCP-only is meaningfully smaller.

---

## 5. Security Considerations

### OAuth token handling
- `qbo_post` must follow the same `tokens` defaulting pattern as `qbo_request`: accept optional `tokens` parameter, fall back to `load_tokens()`. This keeps secrets out of function call arguments in most flows.
- Access tokens expire. If a POST returns 401, `qbo_client` should surface a clear error (not silently retry) because automatic token refresh during a write operation mid-session is risky — the MCP server has no session lifecycle to refresh tokens in.
- `tokens.json` must remain gitignored (already is). The integration test must load it from the file system, not embed credentials.

### Input validation
- `bill_id` and `payment_account_id` are passed into QBO API URL paths and query parameters. They should be validated to be numeric strings (QBO IDs are integers) before use. A non-numeric value should return `BILL_NOT_FOUND` / `INVALID_PAYMENT_ACCOUNT` rather than making an API call with a malformed ID.
- `amount` must be validated as a positive float with at most 2 decimal places. Reject values like `0`, negative numbers, or strings.
- `payment_date` must be validated as a strict ISO 8601 date (`YYYY-MM-DD`) before use in any API call or date arithmetic.
- `memo` should be capped at a reasonable length (e.g., 4000 chars — QBO's limit for `PrivateNote`).

### Injection risks
- QBO's query API uses SQL-like syntax. The existing `query()` function interpolates parameters directly into the SQL string. The new write path does not use `query()`, so there is no new SQL injection surface. However, any future use of `query()` with user-supplied values (e.g., vendor name search) should be noted as a risk.
- The QBO BillPayment POST body is JSON-serialized by `requests` — no string interpolation into the body, so no JSON injection risk as long as `json=payload` is used (not string concatenation).

### Confirmation token security
- Tokens are stored in process memory only — they do not survive a server restart. This is acceptable for a single-process MCP server but means previews are invalidated on restart.
- Tokens are not cryptographically signed in this plan. They are opaque identifiers into a server-side store. This is appropriate for the threat model (local MCP server accessed by a trusted LLM client on the same machine), but if the MCP server is ever exposed over a network (SSE transport mode), consider signing tokens with HMAC using a server secret so that external callers cannot forge or enumerate tokens.
- The 5-minute TTL and single-use constraint are both enforced server-side, not client-side.

### Irreversibility
- The `create_bill_payment` function creates a real accounting record. There is no undo in this implementation. The server should log every call to `create_bill_payment` (token used, bill ID, amount, timestamp, QBO payment ID returned) at INFO level before making the POST, so that incidents can be reconstructed.

---

## 6. Concerns and Open Questions

### On the PRD

**1. Story 2 (pay multiple bills) conflicts with the UX spec's "no bulk payment tool at launch" statement.**
The PRD (Story 2) describes paying all bills for a vendor in one request. The UX spec explicitly defers bulk payment and requires each bill to go through its own preview-confirm-execute cycle. These contradict each other. Recommend the PM clarify before development starts — if Story 2 is in scope for v1, the design spec needs to be updated. If it is deferred, it should be removed from the PRD's acceptance criteria for this release.

**2. Story 4 says the tool accepts `bank_account_id` but the UX spec says the MCP tool accepts `payment_account_id`.**
Minor naming inconsistency between PRD and design. Recommend standardizing on `payment_account_id` (the UX spec term) as it is more general-purpose and consistent with QBO's terminology.

**3. Account balance for preview.**
The UX spec preview shows `payment_account_balance` and `balance_after_payment`. QBO's `Account` object does include `CurrentBalance`, but this is the QBO ledger balance, which may lag the real bank balance (especially in sandbox). The preview should display this caveat to users so they are not misled by a stale figure.

**4. No mention of QBO token refresh.**
Access tokens expire (typically after 60 minutes). The PRD and UX spec do not address what happens if a token expires between `preview_bill_payment` and `create_bill_payment`. This is most relevant for sessions where the user takes a long time to confirm. The MCP server should catch 401 responses and return a clear `QBO_API_ERROR` with a message telling the user to re-authenticate via `qbo_auth.py`.

### On the UX Spec

**5. `DUPLICATE_PAYMENT` requires "explicit override acknowledgment" but no mechanism is defined.**
The spec says duplicate detection requires user acknowledgment before proceeding, but no override parameter or flow is specified. In the current design, `create_bill_payment` only accepts `confirmation_token` and `user_confirmed`. If a duplicate is detected, the user is stuck — they cannot force through the payment even if intentional. Recommend either: (a) add a `force_duplicate: bool = false` parameter to `create_bill_payment`, or (b) treat duplicate detection as a preview-time warning (surfaced in `validation.warnings`) rather than an execution-time hard error, allowing the token to encode the user's awareness of the duplicate.

**6. In-memory token store does not survive server restarts.**
If the MCP server crashes or is restarted between `preview_bill_payment` and `create_bill_payment`, the token is lost and the user must start over. This is probably acceptable but should be documented so users know to re-run the preview if they get a `TOKEN_EXPIRED` error unexpectedly.

**7. No defined behavior for `user_confirmed=false`.**
The spec says the tool must return `USER_NOT_CONFIRMED`. Clarify: does this consume the token (preventing later use) or leave it valid? The plan proposes leaving the token valid on `USER_NOT_CONFIRMED` so the user can retry with `user_confirmed=true` without going back through preview. This should be confirmed.

**8. The `BILL_PARTIALLY_APPLIED` error code has no validation logic defined.**
The error table lists `BILL_PARTIALLY_APPLIED` (triggered by credits/discounts in flight), but the UX spec does not explain how to detect this state from the QBO API. QBO `Bill` objects have a `Balance` field but no explicit flag for in-flight credits. Either define the detection logic or remove this error code from v1.

---

## 7. Testing Strategy (for Test Lead)

### Unit Tests (`tests/test_bill_payment_unit.py`)

Use `unittest.mock.patch` to mock `requests.post` and `requests.get` throughout. No real QBO calls.

**Coverage targets:**

- **Token module (Task 3):** generate → consume happy path; consume expired token; consume already-used token; consume unknown token. These are pure Python — no mocks needed.
- **`get_bill_by_id` (Task 2):** valid bill returned; QBO 400 (not found) mapped to `ValueError`.
- **`preview_bill_payment` (Task 4):** one test per validation failure path (all 6 error codes); happy path with full balance; happy path with partial amount; insufficient funds warning present when account balance is low.
- **`create_bill_payment` (Task 5):** happy path POST succeeds; duplicate detected; QBO returns 400; QBO returns 500.
- **MCP tools (Task 6):** for each tool, test that a client-layer exception is correctly mapped to the right error envelope JSON; test that the success envelope shape matches the spec exactly (all required fields present).

**Key things to assert:**
- Error envelopes always have `status`, `error_code`, `message`, and `recoverable` fields — missing any of these is a contract violation that will confuse LLM clients.
- `user_confirmed=false` returns `USER_NOT_CONFIRMED` without touching the token store.
- `confirmation_token` is absent from the `preview` dict in the preview response (it lives at the top level, not nested inside `preview`).

### Integration Tests (`tests/test_bill_payment_integration.py`)

Run manually against QBO sandbox. Requires `src/tokens.json`.

**Scenarios:**
1. Full happy path: look up a known unpaid sandbox bill → preview → confirm → create → verify payment appears in `get_bill_payments`
2. Pay with partial amount: verify bill balance in QBO decreases by the partial amount (not to zero)
3. Expired token: sleep past 5 minutes (or mock time) and attempt `create_bill_payment` — expect `TOKEN_EXPIRED`
4. Double-create: call `create_bill_payment` twice with the same token — expect `TOKEN_ALREADY_USED` on the second call

**What to avoid:** Do not run integration tests in CI against production QBO. Sandbox only. Gate them behind an environment variable or skip decorator that checks for `tokens.json` presence.

### Manual QA Checklist (before release)

- [ ] Preview a bill from a real sandbox session; confirm the preview numbers match what is visible in the QBO UI
- [ ] Execute a payment; verify the BillPayment record appears in the QBO sandbox UI under the correct vendor
- [ ] Attempt to pay an already-paid bill; verify the `BILL_ALREADY_PAID` error is returned
- [ ] Attempt to pay with a non-bank account ID; verify `INVALID_PAYMENT_ACCOUNT`
- [ ] Attempt to call `create_bill_payment` without first calling `preview_bill_payment` (fabricate a token string); verify `TOKEN_NOT_FOUND`
- [ ] Wait 6 minutes after preview, then attempt to execute; verify `TOKEN_EXPIRED`
- [ ] Verify `get_bill_payments` lists the newly created payment in the same session
