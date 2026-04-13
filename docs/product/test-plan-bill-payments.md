# Test Plan: Bill Payment Feature

**Feature:** Bill Payment Write Operations (MCP Server)  
**Date:** 2026-04-12  
**Author:** Test Lead  
**Based on:** PRD (2026-04-12), UX Spec (2026-04-12), Implementation Plan (2026-04-12)  
**Status:** Draft — pre-implementation

---

## Table of Contents

1. [Test Strategy](#1-test-strategy)
2. [Test Categories](#2-test-categories)
3. [Test Cases](#3-test-cases)
4. [Test Data and Fixtures](#4-test-data-and-fixtures)
5. [Entry and Exit Criteria](#5-entry-and-exit-criteria)
6. [Risk Areas](#6-risk-areas)

---

## 1. Test Strategy

### 1.1 Approach

The bill payment feature introduces the first **write operations** into a previously read-only codebase. This changes the testing calculus significantly: a read test that is wrong produces a wrong answer; a write test that is wrong creates real accounting records or fails to catch a double-payment. Tests must be thorough and deterministic.

The strategy is pyramid-shaped: the majority of coverage lives in isolated unit tests where inputs and outputs are fully controlled, a smaller layer of integration tests exercises real component wiring, and a manual QA pass runs against the live QBO sandbox to confirm the end-to-end flow before any release.

### 1.2 Test Pyramid

```
        /  Manual QA  \          (sandbox, exploratory)
       /  Integration  \         (real token module + mocked HTTP)
      /   Unit Tests    \        (pure Python, all mocked)
```

| Layer | Location | Speed | Isolation | Run in CI? |
|---|---|---|---|---|
| Unit | `tests/test_payment_tokens.py`, `tests/test_qbo_client_bill_payment.py`, `tests/test_mcp_bill_payment_tools.py` | < 1 s/test | Full (no I/O) | Yes |
| Integration | `tests/test_bill_payment_integration.py` | 2–30 s/test | Partial (real token module, mocked HTTP or real QBO) | No — manual only |
| Manual QA | QBO Sandbox session | Minutes | None (real system) | No |

### 1.3 What to Mock vs. What to Keep Real

| Component | Unit Tests | Integration Tests |
|---|---|---|
| QBO HTTP calls (`requests.get`, `requests.post`) | Mock with `unittest.mock.patch` | Mock for most; real for sandbox-tagged tests only |
| `payment_tokens` module | Real (pure Python, no I/O) | Real |
| `qbo_client` functions called inside preview | Mock at the function level (e.g., patch `qbo_client.get_bill_by_id`) | Real client, mocked HTTP |
| `datetime.date.today()` | Mock to pin date for deterministic tests | Real |
| Token TTL clock (`time.time()`) | Mock to simulate expiry without sleeping | Real (or `time.sleep(301)` in sandbox tests) |
| File system (`tokens.json`) | Not used — tests pass `tokens` dict directly | Loaded from `tokens.json` if present; skip if absent |

### 1.4 Framework and Conventions

- **Framework:** `pytest` (already in `tests/requirements.txt`)
- **Mocking:** `unittest.mock.patch` and `pytest-mock` (add `pytest-mock` to `tests/requirements.txt`)
- **Test naming:** `test_<what>_<condition>_<expected_result>` — examples throughout Section 3
- **Fixtures:** Defined in `tests/conftest.py` (see Section 4)
- **Markers:** `@pytest.mark.integration` on all integration tests so they can be excluded from CI with `-m "not integration"`
- **Error envelope contract:** A shared helper assertion `assert_error_envelope(response, error_code)` should live in `tests/helpers.py` to avoid duplicating envelope field checks across every test

---

## 2. Test Categories

### 2.1 Unit Tests

#### 2.1.1 `tests/test_payment_tokens.py` — Token Module (Task 3)

Pure Python. No mocks needed. Covers the module in isolation before anything else depends on it.

Scenarios: generate, consume (happy), consume (expired), consume (already used), consume (unknown), token format, TTL constant, module-level store isolation between tests.

#### 2.1.2 `tests/test_qbo_client_bill_payment.py` — Client Functions (Tasks 1, 2, 4, 5)

Patches `requests.get` / `requests.post` at the `qbo_client` module boundary. Tests the client-layer logic (validation ordering, body construction, duplicate detection) independently of the MCP layer.

Sub-groups:
- `qbo_post` function behavior
- `get_bill_by_id` — found and not-found paths
- `preview_bill_payment` — all six validation branches plus happy paths
- `create_bill_payment` — body construction, duplicate detection, QBO error propagation

#### 2.1.3 `tests/test_mcp_bill_payment_tools.py` — MCP Tools (Task 6)

Patches the underlying `qbo_client` functions and `payment_tokens` functions. Tests that each MCP tool correctly:
- Maps client exceptions to error envelopes
- Returns required JSON fields in every branch
- Never generates a token on a validation failure
- Never touches the token store when `user_confirmed=False`

### 2.2 Integration Tests

#### 2.2.1 `tests/test_bill_payment_integration.py` — Full Flow (Task 8)

Requires `tokens.json` to be present (skip decorator if absent). Exercises the complete preview → confirm → execute cycle against the QBO sandbox. Separate from unit tests; never run in CI.

### 2.3 Manual QA

A checklist of scenarios that require a human with eyes on both the agent and the QBO sandbox UI. See Section 3.3.

---

## 3. Test Cases

### 3.1 Unit Tests

#### Module: `payment_tokens`

| ID | Function | Description | Preconditions | Input | Expected Result | Priority |
|---|---|---|---|---|---|---|
| UT-TOK-01 | `generate_token` | Happy path — token format matches spec | Clean module state | `payload = {"bill_id": "123", "amount": 100.0}` | Returns string matching `prev_[0-9a-f]{12}_[0-9]+`; payload stored in module dict | P0 |
| UT-TOK-02 | `generate_token` | Returned token is unique per call | Two sequential calls with identical payload | Same payload twice | Two different token strings | P0 |
| UT-TOK-03 | `consume_token` | Happy path — first use returns payload | Token generated in same test | Valid unexpired token | Returns original payload dict; token marked used | P0 |
| UT-TOK-04 | `consume_token` | Raises `TokenAlreadyUsedError` on second call | Token already consumed once | Same valid token, second call | `TokenAlreadyUsedError` raised | P0 |
| UT-TOK-05 | `consume_token` | Raises `TokenExpiredError` after TTL | Token generated; `time.time` patched to `generated_at + 301` | Valid token, but clock advanced past 5 min | `TokenExpiredError` raised | P0 |
| UT-TOK-06 | `consume_token` | Raises `TokenNotFoundError` for unknown string | Any state | `"prev_notareal_0000000000"` | `TokenNotFoundError` raised | P0 |
| UT-TOK-07 | `consume_token` | Raises `TokenNotFoundError` for empty string | Any state | `""` | `TokenNotFoundError` raised | P1 |
| UT-TOK-08 | `consume_token` | Token at exactly TTL boundary — expired | `time.time` patched to `generated_at + 300` (boundary) | Token at exactly 300 s old | `TokenExpiredError` raised (boundary is exclusive — >= 300 s is expired) | P1 |
| UT-TOK-09 | `generate_token` | `expires_at` stored as `now + 300` | `time.time` patched | Any payload | `store[token]["expires_at"] == patched_time + 300` | P1 |
| UT-TOK-10 | `consume_token` | `USER_NOT_CONFIRMED` path does not consume token | Token generated; `user_confirmed=False` passed to MCP tool (tested at MCP layer, but token must still be valid after) | Consume not called; token remains in store unused | Token is still consumable (used=False) after a `USER_NOT_CONFIRMED` response at MCP layer | P1 |

**Test function names (examples):**
- `test_generate_token_happy_path_returns_formatted_string`
- `test_consume_token_first_use_returns_payload`
- `test_consume_token_second_use_raises_token_already_used_error`
- `test_consume_token_after_ttl_raises_token_expired_error`
- `test_consume_token_unknown_token_raises_token_not_found_error`

---

#### Module: `qbo_client` — `qbo_post`

| ID | Function | Description | Preconditions | Input | Expected Result | Priority |
|---|---|---|---|---|---|---|
| UT-POST-01 | `qbo_post` | Happy path — returns parsed JSON | `requests.post` mocked to return 200 with JSON body | Valid endpoint, payload dict | Returns parsed response dict | P0 |
| UT-POST-02 | `qbo_post` | Sets `Content-Type: application/json` header | Mock captures request headers | Any call | `Content-Type` header is `application/json` in the captured request | P0 |
| UT-POST-03 | `qbo_post` | Raises `HTTPError` on 400 | `requests.post` mocked to return 400 | Any call | `requests.HTTPError` raised | P0 |
| UT-POST-04 | `qbo_post` | Raises `HTTPError` on 500 | `requests.post` mocked to return 500 | Any call | `requests.HTTPError` raised | P0 |
| UT-POST-05 | `qbo_post` | Uses `tokens` param when provided | Mock captures auth headers | Call with explicit `tokens` dict | Auth header derived from provided tokens, not from `load_tokens()` | P1 |

**Test function names (examples):**
- `test_qbo_post_success_returns_parsed_json`
- `test_qbo_post_sets_content_type_header`
- `test_qbo_post_400_response_raises_http_error`
- `test_qbo_post_500_response_raises_http_error`

---

#### Module: `qbo_client` — `get_bill_by_id`

| ID | Function | Description | Preconditions | Input | Expected Result | Priority |
|---|---|---|---|---|---|---|
| UT-GBB-01 | `get_bill_by_id` | Happy path — returns bill dict | `qbo_request` mocked to return `{"Bill": {...}}` | `bill_id = "123"` | Returns the `Bill` object dict | P0 |
| UT-GBB-02 | `get_bill_by_id` | Raises `ValueError("BILL_NOT_FOUND")` on QBO 400 | `qbo_request` mocked to raise `HTTPError` with 400 status | `bill_id = "999"` | `ValueError` with message `"BILL_NOT_FOUND"` | P0 |
| UT-GBB-03 | `get_bill_by_id` | Non-numeric bill_id raises `ValueError` | No mock needed | `bill_id = "abc"` | `ValueError` raised before any HTTP call is made | P1 |

**Test function names (examples):**
- `test_get_bill_by_id_valid_id_returns_bill_dict`
- `test_get_bill_by_id_not_found_raises_value_error_bill_not_found`
- `test_get_bill_by_id_non_numeric_id_raises_value_error`

---

#### Module: `qbo_client` — `preview_bill_payment`

Each validation step is tested in isolation by making all prior steps succeed and engineering the target step to fail.

| ID | Function | Description | Preconditions | Input | Expected Result | Priority |
|---|---|---|---|---|---|---|
| UT-PRV-01 | `preview_bill_payment` | Happy path — full balance default | Bill balance $1500, account balance $42000, today's date | `bill_id="123"`, `payment_account_id="456"`, no optional params | Returns dict with all required fields; `payment_amount=1500.0`; `is_partial_payment=False`; `warnings=[]` | P0 |
| UT-PRV-02 | `preview_bill_payment` | Happy path — partial amount | Bill balance $1500, `amount=500` | `amount=500.0` | `payment_amount=500.0`; `is_partial_payment=True`; `bill_balance` still shows 1500 | P0 |
| UT-PRV-03 | `preview_bill_payment` | Raises `BILL_NOT_FOUND` when bill absent | `get_bill_by_id` raises `ValueError("BILL_NOT_FOUND")` | Any bill_id | Exception with code `BILL_NOT_FOUND` propagated; no further validation attempted | P0 |
| UT-PRV-04 | `preview_bill_payment` | Raises `BILL_ALREADY_PAID` when balance is 0 | Bill returned with `Balance=0` | `bill_id="123"` | Exception with code `BILL_ALREADY_PAID` | P0 |
| UT-PRV-05 | `preview_bill_payment` | Raises `INVALID_PAYMENT_ACCOUNT` when account not found | `get_accounts` returns empty list for given ID | `payment_account_id="999"` | Exception with code `INVALID_PAYMENT_ACCOUNT` | P0 |
| UT-PRV-06 | `preview_bill_payment` | Raises `INVALID_PAYMENT_ACCOUNT` when account is not Bank type | Account found but `AccountType="Expense"` | Valid `payment_account_id` | Exception with code `INVALID_PAYMENT_ACCOUNT` | P0 |
| UT-PRV-07 | `preview_bill_payment` | Raises `AMOUNT_EXCEEDS_BALANCE` when amount > bill balance | Bill balance $500 | `amount=600.0` | Exception with code `AMOUNT_EXCEEDS_BALANCE` | P0 |
| UT-PRV-08 | `preview_bill_payment` | Raises `PAYMENT_DATE_OUT_OF_RANGE` for date > +30 days | `datetime.date.today` patched to `2026-04-12` | `payment_date="2026-05-13"` (31 days out) | Exception with code `PAYMENT_DATE_OUT_OF_RANGE` | P0 |
| UT-PRV-09 | `preview_bill_payment` | Raises `PAYMENT_DATE_OUT_OF_RANGE` for date > -90 days | `datetime.date.today` patched to `2026-04-12` | `payment_date="2026-01-11"` (91 days ago) | Exception with code `PAYMENT_DATE_OUT_OF_RANGE` | P0 |
| UT-PRV-10 | `preview_bill_payment` | Adds `INSUFFICIENT_FUNDS` warning (not hard error) when account balance < payment amount | Bill balance $1500, account `CurrentBalance=$200` | `amount=1500.0` | Returns preview dict (no exception); `warnings` list contains `"INSUFFICIENT_FUNDS"` | P0 |
| UT-PRV-11 | `preview_bill_payment` | Date at boundary +30 days is accepted | Today patched to `2026-04-12` | `payment_date="2026-05-12"` (exactly +30) | No exception; `payment_date` in returned dict matches input | P1 |
| UT-PRV-12 | `preview_bill_payment` | Date at boundary -90 days is accepted | Today patched to `2026-04-12` | `payment_date="2026-01-12"` (exactly -90) | No exception | P1 |
| UT-PRV-13 | `preview_bill_payment` | Defaults `payment_date` to today when not provided | Today patched to `2026-04-12` | No `payment_date` param | Returned dict `payment_date="2026-04-12"` | P1 |
| UT-PRV-14 | `preview_bill_payment` | `balance_after_payment` computed correctly | Account balance $42000, payment amount $1500 | Full balance params | `balance_after_payment == 40500.0` | P1 |
| UT-PRV-15 | `preview_bill_payment` | `amount=0` is rejected | Any valid bill and account | `amount=0` | Exception raised (not a valid payment amount) | P1 |
| UT-PRV-16 | `preview_bill_payment` | Negative `amount` is rejected | Any valid bill and account | `amount=-50.0` | Exception raised | P1 |
| UT-PRV-17 | `preview_bill_payment` | Validation order — `BILL_NOT_FOUND` checked before account lookup | `get_bill_by_id` raises; `get_accounts` should not be called | Invalid bill_id, valid account_id | `get_accounts` mock is never called | P1 |
| UT-PRV-18 | `preview_bill_payment` | Validation order — `BILL_ALREADY_PAID` checked before account lookup | Bill balance 0; `get_accounts` should not be called | Paid bill | `get_accounts` mock is never called | P1 |

**Test function names (examples):**
- `test_preview_bill_payment_happy_path_full_balance_returns_complete_dict`
- `test_preview_bill_payment_partial_amount_sets_is_partial_payment_true`
- `test_preview_bill_payment_bill_not_found_raises_bill_not_found_error`
- `test_preview_bill_payment_balance_zero_raises_bill_already_paid_error`
- `test_preview_bill_payment_account_not_found_raises_invalid_payment_account_error`
- `test_preview_bill_payment_account_wrong_type_raises_invalid_payment_account_error`
- `test_preview_bill_payment_amount_exceeds_balance_raises_error`
- `test_preview_bill_payment_date_too_far_future_raises_payment_date_out_of_range`
- `test_preview_bill_payment_date_too_far_past_raises_payment_date_out_of_range`
- `test_preview_bill_payment_low_account_balance_adds_insufficient_funds_warning`
- `test_preview_bill_payment_bill_not_found_does_not_call_get_accounts`

---

#### Module: `qbo_client` — `create_bill_payment`

| ID | Function | Description | Preconditions | Input | Expected Result | Priority |
|---|---|---|---|---|---|---|
| UT-CRE-01 | `create_bill_payment` | Happy path — constructs correct QBO body and returns BillPayment | `qbo_post` mocked to return created BillPayment; no duplicate found | Valid payment payload dict | Returns BillPayment dict from QBO; `qbo_post` called once with `"billpayment"` endpoint | P0 |
| UT-CRE-02 | `create_bill_payment` | QBO body contains all required fields | Capture the `qbo_post` call args | Valid payload | POST body contains `PayType`, `VendorRef`, `TotalAmt`, `TxnDate`, `CheckPayment.BankAccountRef`, `Line[0].LinkedTxn[0].TxnId` | P0 |
| UT-CRE-03 | `create_bill_payment` | `Line[0].Amount` matches `TotalAmt` | Valid payload with `amount=750.0` | `amount=750.0` | Both `TotalAmt` and `Line[0].Amount` equal `750.0` in the captured POST body | P0 |
| UT-CRE-04 | `create_bill_payment` | Raises `DuplicatePaymentError` when same bill paid within 24h | `get_bill_payments` returns payment for same bill_id within last 24h | Same bill_id as recent payment | `DuplicatePaymentError` raised; `qbo_post` never called | P0 |
| UT-CRE-05 | `create_bill_payment` | Raises `QBOAPIError` wrapping QBO 400 | `qbo_post` raises `HTTPError` with 400 | Valid payload | `QBOAPIError` raised; original QBO error detail string preserved in exception | P0 |
| UT-CRE-06 | `create_bill_payment` | Raises `QBOAPIError` wrapping QBO 500 | `qbo_post` raises `HTTPError` with 500 | Valid payload | `QBOAPIError` raised | P0 |
| UT-CRE-07 | `create_bill_payment` | Memo appears as `PrivateNote` in QBO body | Valid payload with `memo="Q1 settlement"` | Payload with memo | `PrivateNote` field in POST body equals `"Q1 settlement"` | P1 |
| UT-CRE-08 | `create_bill_payment` | Empty memo sends empty string, not null | Payload with `memo=""` | `memo=""` | `PrivateNote` is `""` in POST body, not `None` | P1 |
| UT-CRE-09 | `create_bill_payment` | Duplicate detection only triggers within 24h window | `get_bill_payments` returns payment for same bill_id but 25 hours ago | Same bill_id | No `DuplicatePaymentError`; `qbo_post` called normally | P1 |
| UT-CRE-10 | `create_bill_payment` | Duplicate detection: different bill_id does not trigger | `get_bill_payments` returns recent payment for a different bill | Different bill_id | No `DuplicatePaymentError` | P1 |

**Test function names (examples):**
- `test_create_bill_payment_happy_path_calls_qbo_post_and_returns_payment`
- `test_create_bill_payment_qbo_body_contains_all_required_fields`
- `test_create_bill_payment_line_amount_matches_total_amt`
- `test_create_bill_payment_duplicate_within_24h_raises_duplicate_payment_error`
- `test_create_bill_payment_duplicate_older_than_24h_does_not_raise`
- `test_create_bill_payment_qbo_400_raises_qbo_api_error_with_detail`
- `test_create_bill_payment_qbo_500_raises_qbo_api_error`

---

#### Module: `qbo_mcp_server` — MCP Tools (Task 6)

These tests patch `qbo_client` and `payment_tokens` functions. They verify the MCP layer's error mapping and JSON envelope contracts.

##### Tool: `get_bill_by_id` (MCP)

| ID | Function | Description | Preconditions | Input | Expected Result | Priority |
|---|---|---|---|---|---|---|
| UT-MCP-GBB-01 | `get_bill_by_id` (MCP) | Happy path — returns bill JSON | `qbo_client.get_bill_by_id` mocked to return bill dict | `bill_id="123"` | Returns valid JSON string containing bill fields | P0 |
| UT-MCP-GBB-02 | `get_bill_by_id` (MCP) | Maps `BILL_NOT_FOUND` to error envelope | `qbo_client.get_bill_by_id` raises `ValueError("BILL_NOT_FOUND")` | `bill_id="999"` | JSON with `status="error"`, `error_code="BILL_NOT_FOUND"`, `message`, `recoverable=True` | P0 |

---

##### Tool: `preview_bill_payment` (MCP)

| ID | Function | Description | Preconditions | Input | Expected Result | Priority |
|---|---|---|---|---|---|---|
| UT-MCP-PRV-01 | `preview_bill_payment` (MCP) | Happy path — returns preview + token | `qbo_client.preview_bill_payment` returns valid preview dict; `payment_tokens.generate_token` returns mock token | Valid params | JSON with `preview`, `validation.valid=true`, `validation.warnings=[]`, `confirmation_token` present | P0 |
| UT-MCP-PRV-02 | `preview_bill_payment` (MCP) | `confirmation_token` is at top level, not inside `preview` | Happy path | Valid params | Parsed JSON: `response["confirmation_token"]` exists; `response["preview"]` does NOT contain `confirmation_token` key | P0 |
| UT-MCP-PRV-03 | `preview_bill_payment` (MCP) | `INSUFFICIENT_FUNDS` warning surfaced in `validation.warnings` | `qbo_client.preview_bill_payment` returns dict with `warnings=["INSUFFICIENT_FUNDS"]` | Valid params | JSON `validation.warnings` contains `"INSUFFICIENT_FUNDS"`; `validation.valid` still `true`; token still generated | P0 |
| UT-MCP-PRV-04 | `preview_bill_payment` (MCP) | Error from client — no token generated | `qbo_client.preview_bill_payment` raises `BILL_NOT_FOUND` | Invalid bill_id | Error envelope returned; `payment_tokens.generate_token` never called | P0 |
| UT-MCP-PRV-05 | `preview_bill_payment` (MCP) | `BILL_ALREADY_PAID` maps to correct envelope | Client raises `BILL_ALREADY_PAID` | Paid bill | `error_code="BILL_ALREADY_PAID"`, `recoverable=False` | P0 |
| UT-MCP-PRV-06 | `preview_bill_payment` (MCP) | `AMOUNT_EXCEEDS_BALANCE` maps to correct envelope | Client raises `AMOUNT_EXCEEDS_BALANCE` | Amount over balance | `error_code="AMOUNT_EXCEEDS_BALANCE"`, `recoverable=True` | P0 |
| UT-MCP-PRV-07 | `preview_bill_payment` (MCP) | `INVALID_PAYMENT_ACCOUNT` maps to correct envelope | Client raises `INVALID_PAYMENT_ACCOUNT` | Bad account | `error_code="INVALID_PAYMENT_ACCOUNT"`, `recoverable=True` | P0 |
| UT-MCP-PRV-08 | `preview_bill_payment` (MCP) | `PAYMENT_DATE_OUT_OF_RANGE` maps to correct envelope | Client raises `PAYMENT_DATE_OUT_OF_RANGE` | Out of range date | `error_code="PAYMENT_DATE_OUT_OF_RANGE"`, `recoverable=True` | P0 |
| UT-MCP-PRV-09 | `preview_bill_payment` (MCP) | Error envelope always has all four required fields | Any error path | Any invalid params | JSON has `status`, `error_code`, `message`, `recoverable` — none missing | P0 |

---

##### Tool: `create_bill_payment` (MCP)

| ID | Function | Description | Preconditions | Input | Expected Result | Priority |
|---|---|---|---|---|---|---|
| UT-MCP-CRE-01 | `create_bill_payment` (MCP) | Happy path — returns success envelope | Token valid; `qbo_client.create_bill_payment` returns BillPayment dict | Valid token, `user_confirmed=True` | JSON with `status="success"`, `payment` object containing all UX spec fields | P0 |
| UT-MCP-CRE-02 | `create_bill_payment` (MCP) | Success envelope contains all required payment fields | Happy path | Valid params | `payment` object has: `payment_id`, `bill_id`, `vendor_name`, `amount_paid`, `payment_date`, `payment_account`, `remaining_bill_balance`, `memo`, `created_at` | P0 |
| UT-MCP-CRE-03 | `create_bill_payment` (MCP) | `user_confirmed=False` returns `USER_NOT_CONFIRMED` without touching token store | Token in store | `user_confirmed=False`, valid token | `error_code="USER_NOT_CONFIRMED"`, `recoverable=True`; `payment_tokens.consume_token` never called | P0 |
| UT-MCP-CRE-04 | `create_bill_payment` (MCP) | `TOKEN_EXPIRED` maps to correct envelope | `consume_token` raises `TokenExpiredError` | Expired token | `error_code="TOKEN_EXPIRED"`, `recoverable=True` | P0 |
| UT-MCP-CRE-05 | `create_bill_payment` (MCP) | `TOKEN_ALREADY_USED` maps to correct envelope | `consume_token` raises `TokenAlreadyUsedError` | Used token | `error_code="TOKEN_ALREADY_USED"`, `recoverable=False` | P0 |
| UT-MCP-CRE-06 | `create_bill_payment` (MCP) | `TOKEN_NOT_FOUND` maps to correct envelope | `consume_token` raises `TokenNotFoundError` | Unknown token string | `error_code="TOKEN_NOT_FOUND"`, `recoverable=True` | P0 |
| UT-MCP-CRE-07 | `create_bill_payment` (MCP) | `DUPLICATE_PAYMENT` maps to correct envelope | `qbo_client.create_bill_payment` raises `DuplicatePaymentError` | Valid token | `error_code="DUPLICATE_PAYMENT"`, `recoverable=True` | P0 |
| UT-MCP-CRE-08 | `create_bill_payment` (MCP) | `QBO_API_ERROR` maps to correct envelope | `qbo_client.create_bill_payment` raises `QBOAPIError` | Valid token | `error_code="QBO_API_ERROR"`, `message` contains QBO detail string | P0 |
| UT-MCP-CRE-09 | `create_bill_payment` (MCP) | Token consumed even when QBO returns error | `consume_token` succeeds; then `qbo_client.create_bill_payment` raises `QBOAPIError` | Valid token | Token is consumed (used=True); `QBO_API_ERROR` envelope returned | P1 |
| UT-MCP-CRE-10 | `create_bill_payment` (MCP) | No payment parameters accepted alongside token | Happy path | Token + `user_confirmed`; any attempt to pass extra params | Tool signature only accepts `confirmation_token` and `user_confirmed` — any extra params would be a tool signature violation | P1 |

**Test function names (examples):**
- `test_create_bill_payment_mcp_happy_path_returns_success_envelope`
- `test_create_bill_payment_mcp_success_envelope_contains_all_required_fields`
- `test_create_bill_payment_mcp_user_not_confirmed_false_does_not_consume_token`
- `test_create_bill_payment_mcp_token_expired_returns_token_expired_error`
- `test_create_bill_payment_mcp_token_already_used_returns_not_recoverable`
- `test_create_bill_payment_mcp_token_not_found_returns_token_not_found_error`
- `test_create_bill_payment_mcp_duplicate_payment_returns_duplicate_error`
- `test_create_bill_payment_mcp_qbo_api_error_includes_upstream_detail`

---

#### Cross-Cutting Contract Tests

These validate the error envelope contract shared across all three MCP tools.

| ID | Description | What to Check | Priority |
|---|---|---|---|
| UT-CON-01 | Every error envelope has exactly: `status`, `error_code`, `message`, `recoverable` | Parse JSON from each error path across all three tools; assert all four keys present | P0 |
| UT-CON-02 | `status` is always the string `"error"` in error envelopes | Same as above | P0 |
| UT-CON-03 | `status` is always the string `"success"` in success responses | All success paths | P0 |
| UT-CON-04 | All tool responses are valid JSON (no `json.loads` exception) | Every test that calls an MCP tool | P0 |
| UT-CON-05 | `recoverable` is boolean, not string | All error envelope tests | P1 |

---

### 3.2 Integration Tests

All integration tests live in `tests/test_bill_payment_integration.py`. They are decorated with `@pytest.mark.integration` and skipped if `tokens.json` is absent.

| ID | Description | Preconditions | Steps | Expected Result | Priority |
|---|---|---|---|---|---|
| IT-01 | Full happy path — full balance payment | QBO sandbox has an unpaid bill with known ID and balance; bank account with sufficient funds | 1. `get_bill_by_id(bill_id)` 2. `preview_bill_payment(bill_id, account_id)` 3. Assert token returned 4. `create_bill_payment(token, user_confirmed=True)` 5. `get_bill_payments()` | Steps 1–4 succeed; `get_bill_payments` returns a record with the QBO payment ID from step 4 | P0 |
| IT-02 | Full happy path — partial amount | Same as IT-01; bill balance >= $100 | Same as IT-01 but with `amount=1.00` (smallest safe amount) | Payment created for $1.00; bill balance in QBO decreases by $1.00 (not to zero) | P0 |
| IT-03 | Token expiry — real 5-minute TTL | Sandbox bill available | 1. `preview_bill_payment(...)` 2. Sleep 301 seconds 3. `create_bill_payment(token, user_confirmed=True)` | `TOKEN_EXPIRED` error envelope | P1 |
| IT-04 | Double-consume same token | Sandbox bill available | 1. Preview to get token 2. `create_bill_payment(token, True)` (succeeds) 3. `create_bill_payment(token, True)` again | Second call returns `TOKEN_ALREADY_USED` | P0 |
| IT-05 | `get_bill_by_id` for a valid sandbox bill | Sandbox bill exists | `get_bill_by_id(known_bill_id)` | Returns dict with correct `Id`, `Balance`, `VendorRef` | P1 |
| IT-06 | `get_bill_by_id` for nonexistent ID | Any state | `get_bill_by_id("999999999")` | `BILL_NOT_FOUND` error | P1 |
| IT-07 | Preview on already-paid bill | Sandbox bill with `Balance=0` (or create one via IT-01 first) | `preview_bill_payment(paid_bill_id, account_id)` | `BILL_ALREADY_PAID` error | P1 |
| IT-08 | `tokens.json` absent — graceful skip | No `tokens.json` on filesystem | Any integration test invoked without `tokens.json` | Test is skipped with a clear message; no exception, no failure | P0 |

**Note on IT-03:** Use `time.sleep(301)` only in the integration test file, never in unit tests. Consider a shorter-TTL override via environment variable (e.g., `PAYMENT_TOKEN_TTL_SECONDS=10`) to avoid 5-minute test wait time in CI-adjacent workflows.

---

### 3.3 Manual QA Checklist

Run against QBO sandbox with a real browser session open alongside to verify UI state. To be completed by a developer or QA engineer before any production deployment.

| ID | Scenario | Steps | Pass Criteria |
|---|---|---|---|
| QA-01 | Preview numbers match QBO UI | Run `preview_bill_payment` for a known sandbox bill; open bill in QBO sandbox UI | `bill_balance` in preview matches what QBO shows for the bill; `payment_account_balance` matches QBO chart of accounts |
| QA-02 | Payment record appears in QBO UI after creation | Run full happy path flow; go to QBO sandbox → Vendors → Bill Payments | BillPayment record exists with correct vendor, amount, date, and bank account; bill shows as paid |
| QA-03 | Partial payment leaves correct remaining balance | Execute partial payment ($1.00 on a $100 bill); check QBO UI | Bill shows remaining balance of $99.00 in QBO |
| QA-04 | `BILL_ALREADY_PAID` error on paid bill | After QA-02, attempt to preview the same bill again | Clear `BILL_ALREADY_PAID` error returned; no new preview or token generated |
| QA-05 | `INVALID_PAYMENT_ACCOUNT` with expense account | Run `get_accounts` to find an Expense account ID; use it as `payment_account_id` in preview | `INVALID_PAYMENT_ACCOUNT` error; message mentions that account must be a Bank type |
| QA-06 | Fabricated token string rejected | Call `create_bill_payment` with `confirmation_token="prev_fakefakefake_0"` | `TOKEN_NOT_FOUND` error; no QBO call made |
| QA-07 | Wait past TTL then execute | Preview a bill; wait 6 minutes; attempt `create_bill_payment` | `TOKEN_EXPIRED` error; message suggests re-running preview |
| QA-08 | `get_bill_payments` shows new payment | Immediately after QA-02, call `get_bill_payments` | The newly created QBO BillPayment ID appears in the results |
| QA-09 | `user_confirmed=False` does not execute | Call `create_bill_payment(token, user_confirmed=False)` with a valid token | `USER_NOT_CONFIRMED` error; token is still valid (call `create_bill_payment` again with `user_confirmed=True` — it should succeed) |
| QA-10 | Duplicate payment warning at preview | Preview the same bill twice within 24 hours of a real payment | Second preview returns `DUPLICATE_PAYMENT` in `validation.warnings`; token is still issued (proceed is allowed) |

---

## 4. Test Data and Fixtures

All fixtures live in `tests/conftest.py` unless noted.

### 4.1 Bill Fixtures

```python
# Unpaid bill — standard case
BILL_UNPAID = {
    "Id": "123",
    "Balance": 1500.00,
    "TotalAmt": 1500.00,
    "TxnDate": "2026-03-01",
    "DueDate": "2026-04-01",
    "VendorRef": {"value": "55", "name": "Acme Corp"},
    "APAccountRef": {"value": "33", "name": "Accounts Payable"},
    "CurrencyRef": {"value": "USD"},
}

# Already paid bill
BILL_PAID = {**BILL_UNPAID, "Id": "124", "Balance": 0.00}

# Partially paid bill (balance < total)
BILL_PARTIAL = {**BILL_UNPAID, "Id": "125", "Balance": 500.00, "TotalAmt": 1500.00}
```

### 4.2 Account Fixtures

```python
# Valid bank account with sufficient funds
ACCOUNT_BANK_SUFFICIENT = {
    "Id": "456",
    "Name": "Business Checking",
    "AccountType": "Bank",
    "AccountSubType": "Checking",
    "CurrentBalance": 42000.00,
    "Active": True,
}

# Valid bank account with insufficient funds
ACCOUNT_BANK_INSUFFICIENT = {
    **ACCOUNT_BANK_SUFFICIENT,
    "Id": "457",
    "CurrentBalance": 200.00,
}

# Non-bank account (Expense type — should be rejected)
ACCOUNT_EXPENSE = {
    "Id": "458",
    "Name": "Office Supplies",
    "AccountType": "Expense",
    "CurrentBalance": 0.00,
    "Active": True,
}
```

### 4.3 Payment Token Fixtures

```python
# Valid unexpired token payload (generated by generate_token)
SAMPLE_PAYMENT_PAYLOAD = {
    "bill_id": "123",
    "vendor_id": "55",
    "vendor_name": "Acme Corp",
    "amount": 1500.00,
    "payment_account_id": "456",
    "payment_account_name": "Business Checking",
    "payment_date": "2026-04-12",
    "memo": "",
}
```

### 4.4 QBO API Response Fixtures

```python
# GET /bill/123 — successful response
QBO_GET_BILL_RESPONSE = {"Bill": BILL_UNPAID, "time": "2026-04-12T14:00:00Z"}

# GET /bill/999 — not found (QBO returns 400 with a Fault body)
QBO_BILL_NOT_FOUND_RESPONSE = {
    "Fault": {
        "Error": [{"Message": "Object Not Found", "code": "610"}],
        "type": "SERVICE",
    }
}

# POST /billpayment — successful creation
QBO_CREATE_BILL_PAYMENT_RESPONSE = {
    "BillPayment": {
        "Id": "789",
        "PayType": "Check",
        "TotalAmt": 1500.00,
        "TxnDate": "2026-04-12",
        "VendorRef": {"value": "55", "name": "Acme Corp"},
        "CheckPayment": {"BankAccountRef": {"value": "456", "name": "Business Checking"}},
        "MetaData": {"CreateTime": "2026-04-12T14:32:00Z"},
    }
}

# GET /query (bill payments list) — for duplicate detection
QBO_BILL_PAYMENTS_EMPTY = {"QueryResponse": {"BillPayment": [], "totalCount": 0}}
QBO_BILL_PAYMENTS_RECENT = {
    "QueryResponse": {
        "totalCount": 1,
        "BillPayment": [
            {
                "Id": "700",
                "TxnDate": "2026-04-12",
                "TotalAmt": 1500.00,
                "Line": [{"LinkedTxn": [{"TxnId": "123", "TxnType": "Bill"}]}],
                "MetaData": {"CreateTime": "2026-04-12T10:00:00Z"},
            }
        ],
    }
}
```

### 4.5 `conftest.py` Pytest Fixtures

```python
# Fixture: fresh token store for each test (avoid state bleed between tests)
@pytest.fixture(autouse=True)
def reset_token_store():
    """Clear the in-memory token store before each test."""
    payment_tokens._token_store.clear()
    yield
    payment_tokens._token_store.clear()

# Fixture: pin today's date
@pytest.fixture
def frozen_today(monkeypatch):
    fixed = datetime.date(2026, 4, 12)
    monkeypatch.setattr(datetime, "date", lambda: fixed)
    return fixed

# Fixture: pin time.time for token expiry control
@pytest.fixture
def frozen_time(monkeypatch):
    fixed_time = 1744684800.0  # 2026-04-12T14:00:00 UTC
    monkeypatch.setattr(time, "time", lambda: fixed_time)
    return fixed_time
```

**Important:** The `autouse=True` on `reset_token_store` is critical. The token module uses a global in-memory dict. Without clearing it between tests, a token generated in test A can be found in test B, causing false positives on `TOKEN_ALREADY_USED` or `TOKEN_NOT_FOUND` tests.

---

## 5. Entry and Exit Criteria

### 5.1 Entry Criteria (when can testing begin?)

Testing of a task may begin when:

- The code for that task is merged to a feature branch (not necessarily main)
- The task's "Done criteria" (from the implementation plan) are self-reported as met by the implementer
- Relevant fixtures and mocks are in place in `conftest.py`

The following tasks can be tested in parallel once their implementations land:

- Token module (Task 3) — no dependencies
- `qbo_post` (Task 1) — no dependencies
- `get_bill_by_id` (Task 2) — no dependencies
- `preview_bill_payment` (Task 4) — after Task 2 is done
- `create_bill_payment` (Task 5) — after Task 1 is done
- MCP tools (Task 6) — after Tasks 2–5 are done

### 5.2 Exit Criteria (when is testing "done"?)

#### P0 Gate (required before any sandbox integration testing)

- [ ] All P0 unit tests pass with `pytest -m "not integration"`
- [ ] Test coverage on `src/payment_tokens.py` is 100% (pure Python with no external dependencies — there is no excuse for less)
- [ ] Test coverage on the new functions in `src/qbo_client.py` (`qbo_post`, `get_bill_by_id`, `preview_bill_payment`, `create_bill_payment`) is >= 90%
- [ ] All error envelope fields (`status`, `error_code`, `message`, `recoverable`) are present in every error path — verified by contract tests UT-CON-01 through UT-CON-05
- [ ] No test uses `time.sleep` (except in integration tests marked `@pytest.mark.integration`)

#### P1 Gate (required before production release)

- [ ] All P0 and P1 unit tests pass
- [ ] Integration tests IT-01 and IT-04 pass against QBO sandbox
- [ ] All Manual QA checklist items QA-01 through QA-10 are checked off and signed by a named person
- [ ] No open bugs with severity P0 or P1
- [ ] `tests/requirements.txt` updated with any new test dependencies (e.g., `pytest-mock`)

#### P2 Gate (nice-to-have, not a release blocker)

- [ ] All P2 unit tests pass
- [ ] Integration tests IT-02, IT-03, IT-05 through IT-08 pass
- [ ] Test run time for unit tests is under 10 seconds total

---

## 6. Risk Areas

The following areas are ranked by likelihood of bugs and severity of impact. Prioritize test review here.

### 6.1 High Risk: Token State Mutation Between Tests

**Why it will break:** `payment_tokens` uses a module-level dict. Python module state persists across tests in a single `pytest` session. A test that generates a token and does not clean up will pollute subsequent tests. The `autouse` fixture in `conftest.py` is the mitigation — but it will only work if `payment_tokens` exposes its `_token_store` dict or provides a `clear()` function. If the implementer makes the store private without an accessor, the fixture cannot clear it.

**Test to watch:** UT-TOK-04 (`TOKEN_ALREADY_USED`) — this test is most sensitive to stale store state.

### 6.2 High Risk: Validation Order in `preview_bill_payment`

**Why it will break:** The implementation plan specifies a strict order for the six validation checks. If the order is wrong, a bill with both `balance=0` AND an invalid account will produce the wrong error code. Tests UT-PRV-17 and UT-PRV-18 explicitly check that early checks short-circuit and prevent later checks from running. If these tests are not present, a wrong ordering could go undetected.

### 6.3 High Risk: `USER_NOT_CONFIRMED` Must Not Consume Token

**Why it will break:** This is a subtle contract point. If the implementer calls `consume_token` before checking `user_confirmed`, then a user who initially says "no" will find their token burned. They would have to re-run preview to pay the bill. Test UT-MCP-CRE-03 catches this by asserting that `consume_token` is never called when `user_confirmed=False`. This test must exist.

### 6.4 High Risk: Error Envelope Contract

**Why it will break:** The four required fields (`status`, `error_code`, `message`, `recoverable`) are the interface contract between the MCP server and LLM clients. If any field is missing from any error path, an LLM parsing the response will either crash or produce a confusing user message. There are 12 distinct error codes across the three tools. It is easy to forget `recoverable` in a new exception handler added late in development. The contract tests (UT-CON-01 through UT-CON-05) and the shared `assert_error_envelope()` helper are the mitigation.

### 6.5 Medium Risk: Duplicate Detection Window

**Why it will break:** The 24-hour window for duplicate detection is calculated relative to `time.time()` at call time. If the comparison is off-by-one (e.g., uses `>=` vs `>`, or compares dates instead of timestamps), recent legitimate payments could be blocked or real duplicates could slip through. Tests UT-CRE-09 (payment at 25h ago is allowed) and UT-CRE-04 (payment at 23h ago is flagged) together pin both sides of the boundary.

### 6.6 Medium Risk: `DUPLICATE_PAYMENT` Is a Preview Warning, Not Execution Error

**Why it will break:** The design decision memo explicitly states that `DUPLICATE_PAYMENT` is a WARNING at preview time (in `validation.warnings`), not a hard error at execution time. However, the UX spec error table and the implementation plan Task 5 ("Raises `DuplicatePaymentError`...") describe it as an execution-time error. This contradiction between documents is the most likely source of a misimplementation. The implementer must clarify with the PM and pick one behavior consistently. Tests must reflect whichever decision is made — but currently the plan calls for execution-time detection in `create_bill_payment`. If the decision changes to preview-time only, UT-CRE-04 and UT-MCP-CRE-07 would need to move to the preview test suite.

### 6.7 Medium Risk: `QBOAPIError` Loses Upstream Detail

**Why it will break:** Test UT-CRE-05 and UT-MCP-CRE-08 both assert that the QBO error detail string is preserved in the exception and surfaced in the error envelope `message`. If the implementer catches `HTTPError` and raises a new `QBOAPIError("")` without preserving the response body, the detail is silently swallowed. This is particularly bad for 400 errors, which contain actionable information from QBO (e.g., "Invalid Reference Id").

### 6.8 Medium Risk: QBO Body Shape Regression

**Why it will break:** The QBO BillPayment POST body has a specific nested structure (`Line[0].LinkedTxn[0].TxnId`, `CheckPayment.BankAccountRef`, etc.). If any key name is wrong or the nesting is off, QBO will return a 400 with a cryptic error. This will not be caught in unit tests unless the test explicitly inspects the captured POST body shape, not just the return value. Tests UT-CRE-02 and UT-CRE-03 cover this — they must inspect the mock call args, not just the return value.

### 6.9 Lower Risk: In-Memory Store Lost on Restart

**Why it is lower risk:** This is documented behavior. Users who encounter `TOKEN_NOT_FOUND` after a server restart will see a recoverable error and can re-run preview. There is no data loss — the actual payment was never made. No test coverage needed; it is a known limitation, not a bug.

### 6.10 Lower Risk: `suggested_action` Field Optional but Inconsistent

**Why it is lower risk:** The UX spec defines `suggested_action` as an optional field in the error envelope. Its presence is not enforced by the contract tests. If it is missing from some error codes but present in others, it is inconsistent but not breaking. Add to P2 backlog if it causes user confusion.

---

*Test plan authored against implementation plan dated 2026-04-12. Re-validate this document if the implementation plan changes significantly, particularly around: validation order in `preview_bill_payment`, `DUPLICATE_PAYMENT` error timing (preview vs. execution), and `USER_NOT_CONFIRMED` token consumption behavior.*
