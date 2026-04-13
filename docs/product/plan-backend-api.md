# Implementation Plan: Backend REST API Service

**Status:** Draft  
**Date:** 2026-04-13  
**Author:** Tech Lead  
**PRD:** `docs/product/prd-backend-api.md`  
**Target branch:** `feature/backend-api`

---

## 1. Architecture Decision: FastAPI + uvicorn vs Flask + Gunicorn/gevent

### Decision: FastAPI + uvicorn

**Rationale:**

The core requirement is streaming SSE responses while executing multi-step tool loops against the Anthropic API and QBO. The two paths are:

**Option A — Flask + Gunicorn/gevent**
- Flask's `Response(stream_with_context(generator))` works for SSE but requires gevent or eventlet monkey-patching to handle >=10 concurrent streaming sessions without thread-per-request overhead.
- Monkey-patching is fragile: it must happen before any other imports (including `anthropic`, `requests`, `sqlite3`), and the `requests` library used in `qbo_client.py` has known gevent incompatibilities that require `grequests` or explicit patching. This is a real operational risk.
- `app.py` already imports `anthropic` and `qbo_client` at module level, making insertion of the monkey-patch tricky and easy to break silently.
- Preserves the existing Flask code, but the POC code is not worth preserving — the PRD is a rewrite, not an extension.

**Option B — FastAPI + uvicorn**
- FastAPI is async-native. `async def` route handlers and `StreamingResponse` with `async for` generators handle SSE without any monkey-patching.
- The Anthropic Python SDK's `.stream()` context manager is compatible with `asyncio` via `await`; no threading gymnastics required.
- `requests` (used in `qbo_client.py`) is a synchronous library. Calling it from an async handler blocks the event loop. This is mitigated by wrapping each `qbo_client` call in `asyncio.to_thread()` — a standard, well-supported pattern that runs the blocking call in a thread pool without monkey-patching.
- uvicorn is a production-grade ASGI server out of the box; no separate WSGI adapter needed.
- FastAPI's built-in `Depends()` injection handles API key auth, request validation (Pydantic), and error responses cleanly.
- Type safety via Pydantic models aligns with the project's Python best-practices requirement for type hints.

**Verdict:** FastAPI + uvicorn. The SSE + concurrent-sessions requirement is the primary driver, and FastAPI handles it without the fragility of gevent monkey-patching. The one trade-off — wrapping `qbo_client` calls in `asyncio.to_thread()` — is a single-line call per tool invocation and is an explicit, visible pattern rather than a hidden global side effect.

**What happens to `app.py`?** It is not deleted. It remains functional as a development/demo shortcut. Its TOOLS list and `execute_tool` dispatcher are replaced by imports from the new shared `src/tools.py` module (Task 1). The new API service lives in `src/api/` as a proper package (see Section 2).

---

## 2. Module Structure

### New files to create

```
src/
├── tools.py                    # TASK 1 — Canonical TOOLS list + execute_tool()
├── api/
│   ├── __init__.py             # Empty
│   ├── main.py                 # FastAPI app factory, lifespan, middleware
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── conversations.py    # POST/GET/DELETE /api/v1/conversations + messages
│   │   └── health.py           # GET /api/v1/health, /api/v1/ready
│   ├── db.py                   # SQLite setup, WAL mode, connection helpers
│   ├── models.py               # Pydantic request/response models
│   ├── agent.py                # Anthropic streaming loop (async); tool dispatch
│   ├── auth.py                 # API key dependency (Depends)
│   ├── errors.py               # Error envelope helpers, exception handlers
│   └── system_prompt.py        # System prompt builder (dynamic date injection)
data/
│   └── .gitkeep                # Ensure directory is tracked; conversations.db goes here
```

### Files to modify

| File | Change |
|---|---|
| `src/app.py` | Remove inline TOOLS list; import `TOOLS` and `execute_tool` from `src/tools.py` |
| `src/chat.py` | Same: remove inline TOOLS list; import from `src/tools.py` |
| `src/requirements.txt` | Add `fastapi`, `uvicorn[standard]`, `pydantic>=2.0` |
| `src/.env` (template in docs) | Document new env vars: `API_KEY`, `DB_PATH`, `PORT`, `LOG_LEVEL`, `LOG_FORMAT` |

### Files NOT touched

- `src/qbo_client.py` — no changes; it is the stable data layer
- `src/payment_tokens.py` — see Section 5.5 for threading concern addressed in `agent.py`
- `src/qbo_mcp_server.py` — explicitly out of scope per PRD
- `src/qbo_auth.py` — no changes
- `src/chat.py` — only the TOOLS import line changes; the CLI behaviour is not modified

---

## 3. Prerequisite: Shared Tools Module (`src/tools.py`)

This is Gate 0. Nothing else starts until this task is merged.

`src/tools.py` must export:

1. **`TOOLS: list[dict]`** — the canonical 13-tool list, matching `qbo_mcp_server.py` exactly. The source of truth for the list is the MCP server's registered tools (which is the most recently maintained copy). The 3 write-operation tools missing from `app.py` and `chat.py` are `get_bill_by_id`, `preview_bill_payment`, and `create_bill_payment`.

2. **`SYSTEM_PROMPT_BASE: str`** — the shared system prompt text, without the date line (date is injected dynamically at request time by `api/system_prompt.py` and by `app.py`/`chat.py` when they call into it).

3. **`execute_tool(tool_name: str, tool_input: dict) -> str`** — the synchronous dispatcher. Returns a JSON string. `app.py` and `chat.py` call this directly. The async API layer wraps it in `asyncio.to_thread()`.

**Consistency enforcement:** A test in `tests/test_tools_sync.py` asserts that the set of tool names in `tools.TOOLS` equals the set of tool names returned by introspecting `qbo_mcp_server`'s registered tools. This prevents silent drift. The test does not invoke QBO; it only compares the name lists.

---

## 4. Task Breakdown

Tasks are ordered by dependency. Each task is one logical commit. Complexity: S = a few hours, M = half a day, L = full day.

### Sprint 1 — Foundation

#### Task 1 — Extract shared tools module `src/tools.py` [S]
**Done criteria:**
- `src/tools.py` exports `TOOLS` (13 tools), `SYSTEM_PROMPT_BASE`, and `execute_tool()`.
- `src/app.py` imports `TOOLS` and `execute_tool` from `tools`; its inline definitions are deleted.
- `src/chat.py` imports `TOOLS` and `execute_tool` from `tools`; its inline definitions are deleted.
- Both `app.py` and `chat.py` still run without error (manual smoke test).
- `tests/test_tools_sync.py` passes, asserting tool name parity with `qbo_mcp_server.py`.

**Files changed:** `src/tools.py` (new), `src/app.py`, `src/chat.py`, `tests/test_tools_sync.py` (new)

#### Task 2 — Project scaffold: `src/api/` package and dependencies [S]
**Done criteria:**
- `src/api/__init__.py`, `src/api/main.py`, all `routers/`, and all support modules created as empty stubs (or with skeleton `pass` bodies).
- `src/requirements.txt` updated with `fastapi`, `uvicorn[standard]`, `pydantic>=2.0`.
- `data/.gitkeep` added.
- `pip install -r src/requirements.txt` succeeds.

**Files changed:** Multiple new files, `src/requirements.txt`

#### Task 3 — Database layer `src/api/db.py` [M]
**Done criteria:**
- `init_db(db_path: Path) -> None` creates the SQLite database file and both tables if they do not exist. Enables WAL journal mode on creation.
- `get_db() -> Generator[sqlite3.Connection, None, None]` is a FastAPI dependency that yields a connection and closes it on teardown.
- Schema:
  ```sql
  CREATE TABLE IF NOT EXISTS conversations (
      id TEXT PRIMARY KEY,           -- UUID as text
      created_at TEXT NOT NULL,      -- ISO-8601
      updated_at TEXT NOT NULL       -- ISO-8601
  );

  CREATE TABLE IF NOT EXISTS messages (
      id TEXT PRIMARY KEY,           -- UUID as text
      conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
      role TEXT NOT NULL,            -- 'user' | 'assistant' | 'tool_use' | 'tool_result'
      content_json TEXT NOT NULL,    -- full Anthropic content block, JSON-serialized
      timestamp TEXT NOT NULL,       -- ISO-8601
      is_internal INTEGER NOT NULL DEFAULT 0  -- 1 for tool_use/tool_result rows
  );

  CREATE INDEX IF NOT EXISTS idx_messages_conversation
      ON messages(conversation_id, timestamp);
  ```
- Unit tests in `tests/test_db.py` cover: init creates tables, WAL mode is set, get_db yields a connection, cascade delete works.

**Files changed:** `src/api/db.py` (implement), `tests/test_db.py` (new)

#### Task 4 — Pydantic models `src/api/models.py` [S]
**Done criteria:**
- Request model: `SendMessageRequest` with `message: str` (max length 10,000 via `Field(max_length=10000)`).
- Response models: `ConversationResponse`, `MessageResponse`, `ConversationListResponse`, `ConversationListItem`, `SendMessageResponse` (non-streaming path), `ErrorDetail`, `ErrorResponse`.
- All models have type hints and docstrings.

**Files changed:** `src/api/models.py`

#### Task 5 — Auth dependency `src/api/auth.py` [S]
**Done criteria:**
- `verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)) -> None` reads `API_KEY` from env. Raises `HTTPException(401)` if missing or mismatched. Never logs the key value.
- Unit test in `tests/test_auth.py`: valid key passes, invalid key raises 401, missing header raises 401.

**Files changed:** `src/api/auth.py`, `tests/test_auth.py` (new)

#### Task 6 — Error handling `src/api/errors.py` [S]
**Done criteria:**
- `api_error(status: int, code: str, message: str, recoverable: bool) -> JSONResponse` builds the standard error envelope.
- `register_exception_handlers(app: FastAPI) -> None` installs handlers for: `RequestValidationError` (422 → 400), unhandled `Exception` (500, logs traceback server-side, returns generic message to client).
- Unit test: validation error and 500 both return the standard envelope shape.

**Files changed:** `src/api/errors.py`, `tests/test_errors.py` (new)

#### Task 7 — System prompt builder `src/api/system_prompt.py` [S]
**Done criteria:**
- `build_system_prompt() -> str` returns `SYSTEM_PROMPT_BASE` (from `tools.py`) with today's date injected and the bill-payment write-operations instruction block appended (mirroring the MCP server's `instructions` field).
- Date injection uses `datetime.date.today().isoformat()` so it is always current at call time.
- Unit test: returned string contains today's date and contains the preview-confirm instruction keyword.

**Files changed:** `src/api/system_prompt.py`, updated `tests/`

---

### Sprint 1 — Core Endpoints

#### Task 8 — Health and readiness endpoints `src/api/routers/health.py` [S]
**Done criteria:**
- `GET /api/v1/health` returns `{"status": "ok"}` with HTTP 200. No auth required.
- `GET /api/v1/ready` checks:
  1. Database is reachable (executes `SELECT 1`).
  2. QBO tokens file exists and is readable (does not make a live QBO call in Phase 1).
  Returns HTTP 200 `{"status": "ok"}` or HTTP 503 `{"status": "degraded", "failed": ["database"|"qbo_tokens"]}`.
- No auth required on readiness endpoint (load balancers do not carry API keys).
- Unit tests with mocked db and token file.

**Files changed:** `src/api/routers/health.py`, `tests/test_health.py` (new)

#### Task 9 — Conversation CRUD `src/api/routers/conversations.py` (non-streaming) [M]
**Done criteria:**
- `POST /api/v1/conversations` → 201, creates row in `conversations` table, returns `ConversationResponse`.
- `GET /api/v1/conversations` → 200, paginated list. `?limit` (default 20, max 100) and `?cursor` (opaque base64-encoded `updated_at + id`). Returns `ConversationListResponse`.
- `GET /api/v1/conversations/{id}` → 200 with message history. Strips `is_internal=1` rows before returning. Returns 404 with standard envelope if not found.
- `DELETE /api/v1/conversations/{id}` → 204. Cascades to messages via FK. Returns 404 if not found.
- All endpoints require `verify_api_key` dependency.
- Integration tests in `tests/test_conversations.py` using a temp SQLite file: happy path and error path for each endpoint.

**Files changed:** `src/api/routers/conversations.py`, `tests/test_conversations.py` (new)

#### Task 10 — FastAPI app factory `src/api/main.py` [S]
**Done criteria:**
- `create_app(db_path: Path | None = None) -> FastAPI` function (factory pattern for testability).
- Lifespan context manager calls `init_db()` on startup.
- Routers for `conversations` and `health` registered under `/api/v1`.
- Exception handlers registered via `errors.register_exception_handlers()`.
- Structured logging middleware: logs method, path, status, duration, conversation_id (extracted from path). Never logs message content. Emits JSON when `LOG_FORMAT=json`.
- `if __name__ == "__main__"` block runs `uvicorn.run(create_app(), host="0.0.0.0", port=PORT)`.

**Files changed:** `src/api/main.py`

---

### Sprint 2 — Streaming Agent Loop

#### Task 11 — Async agent loop `src/api/agent.py` [L]
**Done criteria:**
- `async def run_agent_turn(conversation_id: str, messages: list[dict], db: Connection) -> AsyncGenerator[str, None]` is the core streaming generator. It:
  1. Calls `anthropic.AsyncAnthropic().messages.stream()` with the full message history, TOOLS, and system prompt.
  2. On each streamed text delta, yields an SSE-formatted `token` event.
  3. When a `tool_use` block is detected in the stream: yields a `tool_start` event, then calls `await asyncio.to_thread(execute_tool, tool_name, tool_input)`, then yields a `tool_end` event with a brief summary (first 100 chars of result).
  4. After each tool round-trip, appends the `tool_use` message (marked `is_internal=1`) and `tool_result` message (marked `is_internal=1`) to the `messages` table.
  5. Continues streaming until `stop_reason == "end_turn"`.
  6. Yields a final `done` event with `conversation_id`, `tools_called` list, and `full_text`.
  7. Persists the final assistant text message (marked `is_internal=0`) to the `messages` table.
  8. Updates `conversations.updated_at`.
- On any exception during the loop, yields an `error` SSE event and logs the full traceback at ERROR level. Never propagates the raw exception to the HTTP layer.
- SSE event format:
  ```
  event: <type>\n
  data: <json_payload>\n
  \n
  ```
- Unit tests in `tests/test_agent.py` with mocked `anthropic.AsyncAnthropic` and mocked `execute_tool`. Tests cover: text-only turn, single tool call, multiple sequential tool calls, error path.

**Files changed:** `src/api/agent.py` (new), `tests/test_agent.py` (new)

#### Task 12 — Message endpoint with SSE streaming [M]
**Done criteria:**
- `POST /api/v1/conversations/{id}/messages` added to `src/api/routers/conversations.py`.
- Validates `SendMessageRequest`. Returns 400 if blank, 404 if conversation not found.
- Detects `Accept: application/json` header. If present, runs the agent non-streaming: collects all SSE events internally and returns a `SendMessageResponse` JSON body.
- Otherwise returns `StreamingResponse(media_type="text/event-stream")` backed by `run_agent_turn()`.
- Persists the incoming user message to `messages` table (with `is_internal=0`) before starting the stream.
- Integration test: non-streaming path with mocked Anthropic client returns correct JSON shape. Streaming path test asserts SSE events are well-formed.

**Files changed:** `src/api/routers/conversations.py` (add endpoint), `tests/test_conversations.py` (add cases)

---

### Sprint 2 — Hardening

#### Task 13 — `payment_tokens.py` thread safety [S]
**Done criteria:**
- `payment_tokens.py` is modified to add a `threading.Lock` around all reads and writes to `_store`.
- The existing comment documenting the thread-safety caveat is updated.
- Existing tests still pass. A new test verifies `generate_token` + `consume_token` under concurrent threads without data corruption.

**Files changed:** `src/payment_tokens.py`, `tests/` (new concurrent test)

#### Task 14 — Observability and structured logging [S]
**Done criteria:**
- `src/api/main.py` logging middleware emits one log line per request: `method`, `path`, `status`, `duration_ms`, `conversation_id` (or `null`).
- Tool calls in `agent.py` log: `tool_name`, `duration_ms`, `success: bool`.
- `LOG_FORMAT=json` produces newline-delimited JSON logs (using stdlib `logging` with a custom `JSONFormatter`).
- Log lines at INFO level never contain message content or API keys.
- Unit test: with `LOG_FORMAT=json`, log output is valid JSON.

**Files changed:** `src/api/main.py`, `src/api/agent.py`, new `src/api/logging_config.py`

#### Task 15 — End-to-end integration test and CI parity [M]
**Done criteria:**
- `tests/test_api_integration.py` exercises all 7 endpoints in sequence against an in-memory (`:memory:`) SQLite database and mocked `qbo_client` + `anthropic` clients.
- Covers: create conversation, list conversations, get conversation, send message (non-streaming), send message (streaming events), delete conversation, health, readiness.
- Covers all error paths: 400 (blank message), 400 (message too long), 401 (missing/wrong key), 404 (unknown conversation), 503 (readiness with broken db).
- `python -m pytest tests/` passes end-to-end from a clean checkout.

**Files changed:** `tests/test_api_integration.py` (new)

---

## 5. Key Implementation Details

### 5.1 Conversation State — SQLite Schema

See Task 3 for the DDL. Key design decisions:

- `content_json` stores the full Anthropic SDK content structure (which may be a list of `ContentBlock` objects, or a plain string for user messages). It is serialized with `json.dumps(..., default=str)` to handle SDK types.
- `is_internal = 1` marks rows that must not be returned to API clients (`tool_use` and `tool_result` roles). The `GET /conversations/{id}` handler filters `WHERE is_internal = 0`.
- When rehydrating conversation history for a new Anthropic API call, the handler fetches ALL rows (including `is_internal=1`) so Claude has full tool-call context. The filtering to `is_internal=0` is only for the client-facing response.
- `updated_at` on the `conversations` table is updated atomically with the final message insert (in the same SQLite transaction in `agent.py`) to avoid an inconsistent state where the conversation was updated but `updated_at` was not.
- WAL mode (`PRAGMA journal_mode=WAL`) is set once in `init_db()`. WAL allows concurrent readers while a write is in progress, which matters for the streaming path (reader fetching history while writer appends tool results).

### 5.2 SSE Streaming with the Anthropic SDK

The Anthropic Python SDK's async streaming interface:

```python
async with anthropic.AsyncAnthropic().messages.stream(
    model="claude-sonnet-4-20250514",
    max_tokens=4096,
    system=system_prompt,
    tools=TOOLS,
    messages=messages,
) as stream:
    async for event in stream:
        # event is a typed StreamEvent from the SDK
        ...
```

The key event types to handle:
- `ContentBlockDeltaEvent` with `delta.type == "text_delta"` — emit `token` SSE event.
- `ContentBlockStartEvent` with `content_block.type == "tool_use"` — capture tool name and `id`; emit `tool_start`.
- `InputJsonDeltaEvent` — accumulate partial JSON for the tool input.
- `ContentBlockStopEvent` after a `tool_use` block — the accumulated input JSON is now complete; dispatch the tool call.
- `MessageStopEvent` — emit `done`.

Tool calls inside the streaming loop require the full loop pattern: after dispatching all tools in a round, append tool results to the message list and open a new `stream()` call. This is identical to the synchronous loop in `app.py` but async.

The `StreamingResponse` from FastAPI accepts an `async def` generator:

```python
async def event_stream() -> AsyncGenerator[str, None]:
    async for chunk in run_agent_turn(...):
        yield chunk

return StreamingResponse(event_stream(), media_type="text/event-stream")
```

FastAPI + uvicorn handle the HTTP chunked transfer encoding and keepalive automatically.

### 5.3 Keeping TOOLS in Sync with `qbo_mcp_server.py`

The canonical source for tool names after this implementation is `src/tools.py`. The MCP server (`qbo_mcp_server.py`) defines its tools via `@mcp.tool()` decorators, which is a different registration mechanism that cannot easily import from `tools.py` without restructuring (and restructuring the MCP server is out of scope).

**Mitigation — `tests/test_tools_sync.py`:**

```python
def test_tools_list_matches_mcp_server():
    from tools import TOOLS
    import importlib, inspect, ast

    # Parse qbo_mcp_server.py AST to find all @mcp.tool() decorated functions
    src = Path("src/qbo_mcp_server.py").read_text()
    tree = ast.parse(src)
    mcp_tool_names = {
        node.name for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            (isinstance(d, ast.Attribute) and d.attr == "tool")
            or (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "tool")
            for d in node.decorator_list
        )
    }
    tools_py_names = {t["name"] for t in TOOLS}
    assert tools_py_names == mcp_tool_names, (
        f"tools.py and qbo_mcp_server.py tool sets differ.\n"
        f"In tools.py only: {tools_py_names - mcp_tool_names}\n"
        f"In mcp_server only: {mcp_tool_names - tools_py_names}"
    )
```

This test runs in CI on every commit that touches either file. It uses AST parsing (no import of the MCP server, which has extra dependencies) and requires no live QBO connection.

### 5.4 `payment_tokens.py` — Process-Local In-Memory Store

The current `_store` dict is not thread-safe. Under FastAPI + uvicorn with async handlers, the GIL provides some protection for dict operations, but `asyncio.to_thread()` calls (used for QBO tool dispatch) run on a thread pool, meaning `generate_token` and `consume_token` can be called from worker threads concurrently.

**Plan (Task 13):** Add `threading.Lock` to `payment_tokens.py`. All reads and writes to `_store` acquire the lock. This is sufficient for single-process deployments.

**Process-local scope concern:** The in-memory token store does not survive server restarts. A token generated and a preview shown to the user will be invalid if the server restarts between the preview and the confirmation. This is an acceptable trade-off for Phase 1 (single-instance, low-volume). The user impact is a `TOKEN_NOT_FOUND` error that prompts them to run the preview again. The error message in `create_bill_payment` already handles this case gracefully.

This limitation should be documented in the API response (the `done` event's metadata can include `token_expires_at` so the frontend can warn the user), and the token TTL (currently 5 minutes) is short enough that lost tokens due to restarts are uncommon in practice. Deferring to SQLite or Redis token storage is explicitly a post-Phase-1 concern.

### 5.5 System Prompt — Dynamic Date and Write-Operations Instruction

`src/api/system_prompt.py`:

```python
WRITE_OPS_INSTRUCTION = """
WRITE OPERATIONS — MANDATORY FLOW:
Paying a bill requires two steps that must be followed in order:
1. Call preview_bill_payment to validate the payment and receive a confirmation_token.
   Present the preview details to the user and ask for explicit confirmation before proceeding.
2. Only after the user explicitly confirms, call create_bill_payment with the
   confirmation_token and user_confirmed=true. Never pass user_confirmed=true unless the
   user has genuinely acknowledged the payment details. Tokens expire after 5 minutes.

Never skip the preview step or fabricate a confirmation token.
"""

def build_system_prompt() -> str:
    today = datetime.date.today().isoformat()
    return f"{SYSTEM_PROMPT_BASE}\n\nToday's date is {today}.{WRITE_OPS_INSTRUCTION}"
```

`app.py` and `chat.py` continue to use their own hardcoded system prompts (with the stale date) until a separate cleanup task — that is acceptable since the PRD does not ask us to update those files beyond the TOOLS import.

---

## 6. What NOT to Build in This Phase

The following items appear in or adjacent to the PRD but should be deferred, with rationale:

| Item | Reason to defer |
|---|---|
| **Migrating `app.py` to the new API contract** | `app.py` is a dev/demo tool. Its `/chat` endpoint is not the new API. Updating it beyond the TOOLS import is wasted effort — it will be superseded. |
| **Updating `index.html` to call the new API** | Frontend work; not in scope for the API service sprint. The old `/chat` endpoint remains functional for the UI during transition. |
| **Soft delete / conversation archiving** | PRD explicitly defers this. Hard delete only. |
| **Rate limiting** | PRD explicitly defers. Not needed for single-tenant API key auth. |
| **`chat.py` beyond the TOOLS import** | PRD explicitly states CLI is not affected. |
| **Per-user QBO tokens** | Requires an identity system. Single server-side `tokens.json` is sufficient for Phase 1. |
| **Redis token store for `payment_tokens.py`** | Single-process SQLite deployment does not need it. Add the `threading.Lock` (Task 13) and document the limitation. |
| **`GET /api/v1/ready` making a live QBO API call** | A live QBO health check adds ~500ms latency to readiness probes and may fail due to network conditions unrelated to service health. Check only that the tokens file exists in Phase 1. Add a live check when the readiness endpoint is used in a real deployment pipeline. |
| **Cursor-based pagination using a proper cursor library** | Encode `updated_at + id` as base64 manually. It is not security-sensitive; opacity is sufficient. |

---

## 7. Testing Strategy

### Test file map

| File | What it tests | External calls mocked |
|---|---|---|
| `tests/test_tools_sync.py` | TOOLS list parity between `tools.py` and `qbo_mcp_server.py` (AST-based) | None |
| `tests/test_db.py` | `init_db`, WAL mode, `get_db`, cascade delete | None (uses `":memory:"`) |
| `tests/test_auth.py` | `verify_api_key` with valid/invalid/missing keys | None |
| `tests/test_errors.py` | Error envelope shape for 400, 422, 500 | None |
| `tests/test_health.py` | `/health` always 200; `/ready` 200 and 503 paths | `db`, token file |
| `tests/test_conversations.py` | CRUD endpoints + message endpoint (non-streaming) | `qbo_client`, `anthropic` |
| `tests/test_agent.py` | Streaming loop: text turn, single tool, multi-tool, error | `anthropic.AsyncAnthropic`, `execute_tool` |
| `tests/test_api_integration.py` | All endpoints end-to-end via FastAPI TestClient | `qbo_client`, `anthropic` |
| `tests/test_payment_tokens.py` (existing) | Token generation/consumption — add concurrency test | None |

### Mocking strategy

- **`qbo_client`**: Mock at the module level in test fixtures using `unittest.mock.patch`. Return minimal dict fixtures — no live QBO calls in any test.
- **`anthropic.AsyncAnthropic`**: Mock the `messages.stream()` context manager to yield a predefined sequence of `StreamEvent`-like objects. Use a factory helper in `tests/conftest.py` so individual tests can specify just the text or tool calls they need.
- **SQLite**: All tests use `":memory:"` databases created per-test via a `pytest` fixture that calls `init_db()`. No file I/O in tests.
- **Environment variables**: Set via `monkeypatch.setenv` in fixtures; never rely on a real `src/.env` file being present.

### Coverage target

Per the PRD's success metrics, 100% of endpoints must have at least one happy-path and one error-path test. The target for line coverage on `src/api/` is >= 85%, enforced in CI via `pytest --cov=src/api --cov-fail-under=85`.

---

## 8. Environment Variable Checklist

New variables to add to `src/.env` (and document in README):

```
API_KEY=<secret>          # Required; no default
DB_PATH=data/conversations.db
PORT=5001
LOG_LEVEL=INFO
LOG_FORMAT=text           # or json
```

Existing variables remain unchanged: `QBO_CLIENT_ID`, `QBO_CLIENT_SECRET`, `QBO_REDIRECT_URI`, `QBO_ENVIRONMENT`, `ANTHROPIC_API_KEY`.

---

## 9. Dependencies and Risks

| Item | Risk | Mitigation |
|---|---|---|
| Anthropic SDK version | `anthropic` in `requirements.txt` has no pinned version; the async streaming interface (`AsyncAnthropic`) is available from v0.20+. | Pin to `anthropic>=0.20.0` in `requirements.txt`. Verify in Task 2. |
| `asyncio.to_thread` availability | Requires Python 3.9+. Project already requires 3.9+. | No action needed. |
| FastAPI + uvicorn new to codebase | Team may not be familiar with ASGI lifecycle, `Depends()`, or `lifespan`. | Keep the factory pattern simple. Document the `lifespan` context manager in `main.py` with a clear comment block. |
| SQLite WAL + concurrent streaming writes | Multiple simultaneous streaming sessions each write rows during tool calls. SQLite WAL handles this but concurrent writes will serialize at the WAL level. | Acceptable for <= 10 concurrent sessions (PRD target). WAL write lock contention only affects tool-result inserts (< 1ms each), not the streaming text path. |
| `payment_tokens._store` process-locality | Token store is lost on restart. | Document in API error responses (Task 12). Thread-safety fix in Task 13. |
| Tool name drift with MCP server | Future sprint adds a tool to MCP without updating `tools.py`. | `test_tools_sync.py` catches this in CI. Make it a required check. |

---

## 10. Complexity Estimates

| Task | Complexity | Sprint |
|---|---|---|
| Task 1 — `src/tools.py` extraction | S | 1 |
| Task 2 — Scaffold | S | 1 |
| Task 3 — `db.py` | M | 1 |
| Task 4 — Pydantic models | S | 1 |
| Task 5 — Auth | S | 1 |
| Task 6 — Error handling | S | 1 |
| Task 7 — System prompt | S | 1 |
| Task 8 — Health endpoints | S | 1 |
| Task 9 — Conversation CRUD | M | 1 |
| Task 10 — App factory | S | 1 |
| Task 11 — Async agent loop | L | 2 |
| Task 12 — Message + SSE endpoint | M | 2 |
| Task 13 — Token thread safety | S | 2 |
| Task 14 — Structured logging | S | 2 |
| Task 15 — Integration tests | M | 2 |

**Total: 4S + 4M + 1L per sprint, across 2 sprints.** This aligns with the PRD's 2-sprint effort estimate.

---

## 11. Open Questions

1. **`tokens.json` location for the API service:** `qbo_client.py` resolves the token file relative to `os.path.dirname(__file__)`, which means it always looks in `src/`. If the API is launched from the project root (`python -m api.main`), this still works as long as `src/` is on `PYTHONPATH`. Confirm the launch command and update `TOKEN_FILE` path logic in `qbo_client.py` if needed before Task 11.

2. **Non-streaming `Accept: application/json` path under tool loops:** The non-streaming path buffers all SSE events and reconstructs the response. The `tools_called` list must include tools from all rounds (not just the first), since Claude may invoke tools in multiple sequential rounds. Confirm this is handled correctly in Task 12 by reviewing `run_agent_turn` output with a multi-round mock.

3. **`data/` directory creation:** `DB_PATH` defaults to `data/conversations.db`. `init_db()` should call `Path(db_path).parent.mkdir(parents=True, exist_ok=True)` before creating the database file. Add to Task 3.
