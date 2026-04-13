# PRD: Backend REST API Service for Finance Agent

**Status:** Draft
**Date:** 2026-04-13
**Author:** Product
**Scope:** New `src/api.py` (or equivalent module); replaces and supersedes the ad-hoc chat logic in `src/app.py`

---

## 1. Problem Statement

The current Flask server in `src/app.py` was written as a proof-of-concept to validate that the QBO tools and Anthropic API could work together. It has several structural problems that block it from being the foundation of a real product:

- **No streaming.** The server blocks until Claude finishes its full multi-step tool loop, then returns one large response. For queries that require multiple QBO tool calls, this can take 10–20 seconds with no feedback to the user.
- **Conversation state is process-local and unrecoverable.** The `conversations` dict lives in RAM. A server restart or horizontal scale-out silently drops every active session.
- **No separation of concerns.** The same file owns HTTP routing, Anthropic API calls, tool dispatch, and session management. There is no clean layer boundary a frontend contract can rely on.
- **TOOLS list is duplicated.** `app.py`, `chat.py`, and `qbo_mcp_server.py` each maintain their own copy. The MCP server now has 13 tools (including the bill payment write operations added in the previous sprint); `app.py` still has only 10.
- **No authentication.** Any client that can reach port 5001 gets full QBO read/write access.
- **No structured error responses.** Errors are inconsistently formatted — some are plain strings, some are JSON objects.

The job-to-be-done:

> **When I am building or operating the Finance Agent UI, I want a reliable, well-defined HTTP API that handles conversation state, calls Claude with the right tools, and streams responses back — so that the frontend is a thin presentation layer and all intelligence stays server-side.**

---

## 2. Goals

1. Provide a production-grade REST API that a React/Next.js (or any) frontend can call without knowing anything about Anthropic or QuickBooks internals.
2. Support streaming responses so the UI can render Claude's reply incrementally.
3. Persist conversation history in a way that survives server restarts and supports multiple concurrent users.
4. Expose a consistent, machine-readable error format across all endpoints.
5. Keep `qbo_client.py` as the single source of truth for QBO calls; the API service delegates to it directly (same pattern as today, but with the full 13-tool set).
6. Lay the groundwork for per-user authentication without requiring a full identity system in this phase.

---

## 3. User Stories

### Story 1 — Send a message and receive a streaming reply

**As an** AP clerk using the Finance Agent web app,
**I want** my message to start rendering on screen as soon as Claude begins its answer,
**so that** I am not staring at a spinner for 15 seconds while waiting for a full response.

**Acceptance Criteria:**

- `POST /api/v1/conversations/{id}/messages` accepts a JSON body with `{"message": "..."}`.
- The endpoint returns a `text/event-stream` (Server-Sent Events) response by default.
- Tokens arrive as SSE events of type `token` as soon as Claude emits them.
- A final SSE event of type `done` signals end of stream, carrying metadata: `conversation_id`, list of tool names called during the turn, and the full assistant text.
- A non-streaming path is available via `Accept: application/json` header for clients that prefer a single response (e.g., CLI tools, integration tests).
- If Claude invokes QBO tools during the turn, the frontend receives intermediate SSE events of type `tool_start` (tool name) and `tool_end` (tool name, brief result summary) so the UI can display "Checking unpaid bills..." while waiting.
- The endpoint returns HTTP 400 with a structured error body if `message` is blank.
- The endpoint returns HTTP 404 if `{id}` does not correspond to a known conversation.

---

### Story 2 — Start a new conversation

**As an** AP clerk,
**I want** to begin a fresh chat session with the assistant,
**so that** context from a previous session does not bleed into my current task.

**Acceptance Criteria:**

- `POST /api/v1/conversations` creates a new conversation record and returns its `id`, `created_at`, and an empty `messages` array.
- The conversation ID is a UUID.
- No message is sent to Claude at creation time; the conversation is just scaffolded.
- The response is HTTP 201.

---

### Story 3 — Resume a conversation

**As an** AP clerk,
**I want** to pick up where I left off if I close the browser and come back,
**so that** I do not have to re-explain context in every session.

**Acceptance Criteria:**

- `GET /api/v1/conversations/{id}` returns the full conversation record: `id`, `created_at`, `updated_at`, and the ordered list of `messages` (each with `role`, `content`, and `timestamp`).
- Tool-use messages (the internal Anthropic `tool_use` / `tool_result` message pairs) are not exposed in the messages array; only user and assistant text messages are returned to the client.
- The endpoint returns HTTP 404 if the conversation does not exist.
- Conversation history is stored in a way that persists across server restarts (see Section 5, Non-Functional Requirements).

---

### Story 4 — List recent conversations

**As an** AP clerk,
**I want** to see a list of my recent chats,
**so that** I can navigate back to a specific session without memorising UUIDs.

**Acceptance Criteria:**

- `GET /api/v1/conversations` returns a paginated list of conversations, ordered by `updated_at` descending.
- Each item in the list includes: `id`, `created_at`, `updated_at`, and `preview` (first 100 characters of the first user message, or empty string if no messages yet).
- Supports `?limit=N` (default 20, max 100) and `?cursor=<opaque_string>` for cursor-based pagination.
- Returns HTTP 200 with an empty `data` array if no conversations exist.

---

### Story 5 — Reset / delete a conversation

**As an** AP clerk,
**I want** to delete a conversation I no longer need,
**so that** my conversation list stays tidy.

**Acceptance Criteria:**

- `DELETE /api/v1/conversations/{id}` deletes the conversation and all its messages.
- Returns HTTP 204 on success.
- Returns HTTP 404 if the conversation does not exist.
- This is a hard delete; there is no soft-delete or recovery in this phase.

---

### Story 6 — Consistent error responses

**As a** frontend engineer integrating the API,
**I want** all errors to follow the same JSON structure,
**so that** I can write one error-handling path in the client and not special-case every endpoint.

**Acceptance Criteria:**

- All error responses use HTTP 4xx or 5xx and return a JSON body matching:
  ```json
  {
    "error": {
      "code": "CONVERSATION_NOT_FOUND",
      "message": "No conversation with id abc123 exists.",
      "recoverable": true
    }
  }
  ```
- `code` is a stable, machine-readable string (SCREAMING_SNAKE_CASE).
- `recoverable` is a boolean indicating whether the client can meaningfully retry.
- 5xx errors do not leak stack traces or internal details to the client; they are logged server-side only.

---

### Story 7 — Health and readiness checks

**As a** DevOps engineer deploying the service,
**I want** standard health endpoints,
**so that** load balancers and orchestrators can determine whether the instance is ready to serve traffic.

**Acceptance Criteria:**

- `GET /api/v1/health` returns HTTP 200 with `{"status": "ok"}` if the service is running.
- `GET /api/v1/ready` returns HTTP 200 if the service can reach both the Anthropic API and QBO (or the configured storage backend). Returns HTTP 503 if any dependency is unavailable, with a body identifying which dependency failed.

---

## 4. Functional Requirements

### 4.1 Endpoint Summary

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/conversations` | Create a new conversation |
| `GET` | `/api/v1/conversations` | List conversations (paginated) |
| `GET` | `/api/v1/conversations/{id}` | Fetch a conversation with full message history |
| `DELETE` | `/api/v1/conversations/{id}` | Delete a conversation |
| `POST` | `/api/v1/conversations/{id}/messages` | Send a message; streams reply via SSE |
| `GET` | `/api/v1/health` | Liveness probe |
| `GET` | `/api/v1/ready` | Readiness probe |

### 4.2 Streaming Protocol (SSE)

The message endpoint uses Server-Sent Events over a persistent HTTP connection. Event types:

| Event type | Payload | When emitted |
|---|---|---|
| `tool_start` | `{"tool": "get_unpaid_bills"}` | Claude decides to call a QBO tool |
| `tool_end` | `{"tool": "get_unpaid_bills", "summary": "Returned 4 unpaid bills"}` | Tool call returns |
| `token` | `{"text": "You have 4 unpaid..."}` | Each streamed text token from Claude |
| `done` | `{"conversation_id": "...", "tools_called": [...], "full_text": "..."}` | End of turn |
| `error` | `{"code": "...", "message": "...", "recoverable": true}` | Any error during the turn |

For the non-streaming path (`Accept: application/json`), the response body is:

```json
{
  "conversation_id": "uuid",
  "message": {
    "role": "assistant",
    "content": "...",
    "timestamp": "ISO-8601"
  },
  "tools_called": ["get_unpaid_bills"]
}
```

### 4.3 Tool Integration

The backend service calls `qbo_client.py` directly (not via MCP stdio/SSE) for tool execution, mirroring the current `app.py` pattern. This avoids the overhead of spawning an MCP subprocess per request.

The TOOLS list exposed to Claude must match the full set of 13 tools currently registered in `qbo_mcp_server.py`, including the bill payment write operations (`preview_bill_payment`, `create_bill_payment`). The current `app.py` TOOLS list (10 tools) is stale and must not be reused as-is.

A single canonical TOOLS definition will live in a shared module (e.g., `src/tools.py`) so that `app.py`, `chat.py`, and the API service do not drift independently.

### 4.4 Conversation State Storage

Phase 1 (this PRD): SQLite via a lightweight ORM (e.g., SQLAlchemy Core or `sqlite3` directly). The database file path is configurable via environment variable `DB_PATH` (default: `data/conversations.db`). This satisfies the persistence requirement and is zero-ops for a single-instance deployment.

The data model for Phase 1:

- `conversations(id UUID PK, created_at, updated_at)`
- `messages(id UUID PK, conversation_id FK, role TEXT, content_json TEXT, timestamp)`

`content_json` stores the full Anthropic message content block (which may be a string or a list of content blocks) as JSON. The API layer strips internal tool-use blocks before returning to clients.

### 4.5 Authentication

Phase 1: API key authentication via `Authorization: Bearer <key>` header. A single server-side API key is read from the `API_KEY` environment variable. Requests without a valid key receive HTTP 401. This is a placeholder mechanism sufficient for single-tenant internal use.

Full multi-user authentication (OAuth2, JWT, per-user QBO tokens) is explicitly out of scope for this phase (see Section 6).

### 4.6 System Prompt

The system prompt used in `app.py` is carried over with two changes:
- The hardcoded `Today's date is 2026-04-12` line is replaced with a dynamic date injection at request time.
- A write-operations section is added (mirroring the MCP server's `instructions` field) to guide Claude on the mandatory preview-confirm flow before executing bill payments.

---

## 5. Non-Functional Requirements

### 5.1 Performance

| Requirement | Target |
|---|---|
| Time-to-first-token (streaming) | < 3 seconds from request receipt to first `token` SSE event, under normal QBO API response times |
| Non-streaming response time (P95) | < 20 seconds for queries requiring up to 3 sequential tool calls |
| Concurrent conversations | Support >= 10 concurrent streaming sessions on a single-core instance without request queuing |

The Flask development server (`app.run()`) does not meet the concurrency requirement. The service must be run behind a WSGI server that supports concurrent requests (e.g., Gunicorn with gevent or eventlet workers) or migrated to an async framework (e.g., FastAPI + uvicorn). The choice of WSGI/ASGI server is an implementation decision for the Tech Lead.

### 5.2 Security

- All communication between client and API must be over HTTPS in any non-local environment.
- The `API_KEY` environment variable must never be logged or included in error responses.
- QBO OAuth tokens (`tokens.json`) remain on the server; they are never returned to or accepted from API clients.
- The API does not accept QBO credentials from clients; it uses the server-side credentials only.
- Conversation content must not be logged at INFO level in production (financial data is sensitive). Debug-level logging of message content is acceptable in non-production environments only.
- Input length limit: user messages are capped at 10,000 characters. Requests exceeding this limit return HTTP 400.

### 5.3 Reliability

- The service must return a structured error response (not crash) if the Anthropic API returns a non-200 status or times out.
- The service must return a structured error response if a QBO tool call fails (e.g., QBO API unavailable, token expired).
- The SQLite database file must not be the same file used by any other process concurrently. WAL mode is recommended to allow concurrent reads.

### 5.4 Observability

- All requests must be logged with: method, path, HTTP status, duration, and conversation ID (never message content at INFO level).
- Tool calls must be logged with: tool name, duration, and success/failure.
- The service must emit structured logs (JSON) when `LOG_FORMAT=json` is set in the environment. Human-readable logs are the default.

### 5.5 Configurability

All environment-specific values must be read from environment variables (or `src/.env` via `python-dotenv`). No hardcoded ports, paths, or keys. New required variables for this service:

| Variable | Description | Default |
|---|---|---|
| `API_KEY` | Bearer token required on all API requests | None (required) |
| `DB_PATH` | Path to SQLite database file | `data/conversations.db` |
| `PORT` | Port the HTTP server listens on | `5001` |
| `LOG_LEVEL` | Logging level | `INFO` |
| `LOG_FORMAT` | `text` or `json` | `text` |

---

## 6. Out of Scope

The following are explicitly NOT part of this PRD:

- **Multi-user / per-user authentication.** Phase 1 uses a single server-side API key. User accounts, OAuth2 login flows, and per-user QBO token management are deferred.
- **WebSocket support.** SSE is sufficient for streaming in this phase. WebSockets add protocol complexity with no functional gain for a unidirectional stream.
- **Message editing or regeneration.** Users cannot edit a sent message or ask Claude to regenerate its last response via the API. This is a future UX enhancement.
- **Conversation sharing or export.** No endpoints for exporting conversation history to PDF, CSV, or a shareable link.
- **Rate limiting.** A single-tenant deployment with API key auth does not require per-client rate limits in Phase 1. This must be added before any public or multi-tenant rollout.
- **Migrating `chat.py`.** The standalone CLI chat interface is not affected by this PRD. It continues to work as-is.
- **Migrating the MCP server.** `qbo_mcp_server.py` is unaffected. It remains the tool layer for Claude Desktop / Claude Code users.
- **Message attachments or file uploads.** Text-only conversations for this phase.
- **Soft delete or conversation archiving.** `DELETE` is a hard delete.
- **Horizontal scaling with shared session state.** SQLite is single-process. Redis or a cloud database would be required for multi-instance deployments; that is a future infrastructure concern.

---

## 7. RICE Score

| Factor | Estimate | Rationale |
|---|---|---|
| **Reach** | 10 / 10 | Affects every user of the web UI — there is no web product without this API layer |
| **Impact** | 9 / 10 | Unblocks streaming (removes the biggest UX pain point), fixes session persistence, and enables any future frontend work |
| **Confidence** | 9 / 10 | All component parts exist (Flask, Anthropic SDK streaming, SQLite, qbo_client). This is architectural restructuring, not net-new capability |
| **Effort** | 2 sprints | One sprint for core endpoints + streaming; one sprint for persistence layer, auth, error handling, and test coverage |

**RICE Score = (10 x 9 x 0.9) / 2 = 40.5**

This is the highest-scoring item in the backlog because it is an enabler: nothing else in the web product roadmap can ship reliably without it.

---

## 8. Success Metrics

### Primary (technical quality)

| Metric | Target | Measurement |
|---|---|---|
| Time-to-first-token | < 3 seconds (P95) | Request logs, duration to first `token` event |
| API error rate (5xx) | < 1% of requests | HTTP status code distribution in logs |
| Session persistence | 0 conversations lost on server restart | Integration test: create conversation, restart server, fetch conversation |

### Secondary (developer experience)

| Metric | Target | Measurement |
|---|---|---|
| Frontend integration time | A new frontend engineer can call all 5 core endpoints with a working demo within 4 hours using only the API contract | Measured at first frontend sprint |
| Endpoint coverage in tests | 100% of endpoints have at least one happy-path and one error-path integration test | Test report |

### Guardrail (safety — inherited from bill payments PRD)

| Metric | Alert Threshold | Action |
|---|---|---|
| Unintended bill payment executions | 0 tolerance | Any write to QBO not preceded by a preview confirmation event triggers incident review |

---

## 9. Dependencies and Risks

| Item | Type | Notes |
|---|---|---|
| Anthropic SDK streaming support | Dependency | `anthropic` Python SDK supports `stream()` context manager; confirm version in `src/requirements.txt` supports SSE streaming before implementation starts |
| Flask streaming via `stream_with_context` | Dependency | Works with Flask + Gunicorn/gevent; does not work with the default threaded dev server |
| SQLite WAL mode | Dependency | Must be explicitly enabled on database creation; default journal mode causes write contention |
| Shared TOOLS module | Dependency | `src/tools.py` must be created and `app.py`, `chat.py` updated to import from it before the API service is built, to avoid a third divergent copy |
| Canonical TOOLS list drift | Risk | If `qbo_mcp_server.py` adds new tools in a future sprint and `src/tools.py` is not updated, the web API will silently be missing those capabilities. Mitigation: add a CI check or test that asserts both lists have the same tool names. |
| SQLite in production | Risk | SQLite is adequate for single-instance, low-concurrency deployments. If the product scales to multiple server instances or high concurrent load, this must be replaced with PostgreSQL. This risk is accepted for Phase 1 with an explicit revisit trigger at > 5 concurrent users or > 1 server instance. |
