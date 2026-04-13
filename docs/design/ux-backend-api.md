# UX Spec: Backend REST API — Developer Experience & API Contract

**Status:** Draft
**Date:** 2026-04-13
**Author:** Design Manager
**Scope:** `src/api.py` (new service replacing `src/app.py` chat logic)
**PRD Reference:** `docs/product/prd-backend-api.md`

---

## Purpose of This Document

This is a developer-experience (DX) specification. The "user" is a frontend engineer consuming the API. Decisions in this document govern:

- Exact request and response shapes for every endpoint
- The Server-Sent Events (SSE) streaming event protocol
- The conversation and message object models
- How errors are communicated (structure, codes, recoverability signals)
- How authentication works from the client perspective
- Rationale for every non-obvious shape decision

The frontend team implements against this document. If a shape is underspecified here, raise it before writing a line of client code.

---

## 1. Global Conventions

### 1.1 Base URL

All API endpoints are prefixed with `/api/v1`. No trailing slashes.

```
http://localhost:5001/api/v1
```

### 1.2 Content Type

- All request bodies: `Content-Type: application/json`
- All non-streaming responses: `Content-Type: application/json`
- Streaming responses: `Content-Type: text/event-stream`

### 1.3 Authentication

Every request except `/api/v1/health` requires an `Authorization` header:

```
Authorization: Bearer <api_key>
```

Missing or invalid tokens return HTTP 401. See Section 5 for full auth flow.

### 1.4 Date and Time Format

All timestamps are ISO 8601 strings in UTC with millisecond precision:

```
"2026-04-13T14:23:01.412Z"
```

Clients must treat all timestamps as UTC. The `Z` suffix is always present.

### 1.5 Identifiers

All IDs are UUIDs (version 4), lowercase, hyphenated:

```
"3fa85f64-5717-4562-b3fc-2c963f66afa6"
```

### 1.6 Versioning

The `/api/v1` prefix allows a future `/api/v2` without breaking existing clients. No version negotiation is done via headers in Phase 1.

---

## 2. API Contract

### 2.1 Endpoint Index

| Method   | Path                                        | Auth Required | Description                           |
|----------|---------------------------------------------|---------------|---------------------------------------|
| `POST`   | `/api/v1/conversations`                     | Yes           | Create a new conversation             |
| `GET`    | `/api/v1/conversations`                     | Yes           | List conversations (paginated)        |
| `GET`    | `/api/v1/conversations/{id}`                | Yes           | Fetch a conversation with messages    |
| `DELETE` | `/api/v1/conversations/{id}`                | Yes           | Delete a conversation                 |
| `POST`   | `/api/v1/conversations/{id}/messages`       | Yes           | Send a message; stream reply via SSE  |
| `GET`    | `/api/v1/health`                            | No            | Liveness probe                        |
| `GET`    | `/api/v1/ready`                             | No            | Readiness probe                       |

---

### 2.2 POST /api/v1/conversations

Creates a new conversation. No message is sent to Claude at this step.

**Request**

No body required. An empty body or `{}` is accepted.

```http
POST /api/v1/conversations
Authorization: Bearer <key>
Content-Type: application/json
```

**Response — 201 Created**

```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "created_at": "2026-04-13T14:23:01.412Z",
  "updated_at": "2026-04-13T14:23:01.412Z",
  "messages": []
}
```

**Response — 401 Unauthorized**

See Section 4.

**Design rationale:** The creation step is separated from the first message so the client can obtain a `conversation_id` before the user types anything. This allows the UI to optimistically render a conversation shell, and means a connection error on the first message does not leave the client without an ID to retry with. `updated_at` equals `created_at` on creation — it is updated whenever a message is appended.

---

### 2.3 GET /api/v1/conversations

Returns a paginated list of conversations, newest first.

**Request**

```http
GET /api/v1/conversations?limit=20&cursor=<opaque_string>
Authorization: Bearer <key>
```

Query parameters:

| Parameter | Type    | Default | Max   | Description                                         |
|-----------|---------|---------|-------|-----------------------------------------------------|
| `limit`   | integer | `20`    | `100` | Number of conversations to return                   |
| `cursor`  | string  | (none)  | —     | Opaque pagination cursor from previous response     |

**Response — 200 OK**

```json
{
  "data": [
    {
      "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "created_at": "2026-04-13T14:23:01.412Z",
      "updated_at": "2026-04-13T14:45:22.100Z",
      "preview": "What bills are overdue?"
    },
    {
      "id": "8db1a2c3-4e5f-6789-abcd-ef0123456789",
      "created_at": "2026-04-12T09:10:00.000Z",
      "updated_at": "2026-04-12T09:18:41.300Z",
      "preview": "Show me our total accounts payable"
    }
  ],
  "pagination": {
    "has_more": true,
    "next_cursor": "eyJpZCI6IjhkYjFhMmMzIn0"
  }
}
```

When no conversations exist:

```json
{
  "data": [],
  "pagination": {
    "has_more": false,
    "next_cursor": null
  }
}
```

**Field definitions:**

- `preview`: First 100 characters of the first user message in the conversation. Empty string `""` if the conversation has no messages yet.
- `next_cursor`: Base64-encoded opaque string. Pass as `?cursor=` on the next request. Null when `has_more` is false.
- Clients must not parse or construct cursor values. Treat them as opaque strings.

**Response — 400 Bad Request** — if `limit` is not a positive integer or exceeds 100.

**Design rationale:** Cursor-based pagination is chosen over page-number pagination because the conversation list is ordered by `updated_at` (mutable). Page-number pagination produces inconsistent results when items shift position between pages — a known problem in chat UIs where the user is actively writing. Cursors are stable. The envelope wraps `data` and `pagination` as top-level siblings rather than embedding pagination inside `data` so that clients can destructure them independently without traversal.

---

### 2.4 GET /api/v1/conversations/{id}

Returns the full conversation record, including all user and assistant messages.

**Request**

```http
GET /api/v1/conversations/3fa85f64-5717-4562-b3fc-2c963f66afa6
Authorization: Bearer <key>
```

**Response — 200 OK**

```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "created_at": "2026-04-13T14:23:01.412Z",
  "updated_at": "2026-04-13T14:45:22.100Z",
  "messages": [
    {
      "id": "a1b2c3d4-e5f6-7890-abcd-ef0123456789",
      "role": "user",
      "content": "What bills are overdue?",
      "timestamp": "2026-04-13T14:23:15.000Z"
    },
    {
      "id": "b2c3d4e5-f6a7-8901-bcde-f01234567890",
      "role": "assistant",
      "content": "You have **3 overdue bills** totalling $4,821.00:\n\n| Vendor | Due Date | Amount |\n|---|---|---|\n| Acme Corp | Apr 1 | $2,100.00 |\n...",
      "timestamp": "2026-04-13T14:23:28.412Z",
      "tools_called": ["get_unpaid_bills"]
    }
  ]
}
```

**Field definitions — message object:**

| Field          | Type             | Present on   | Description                                              |
|----------------|------------------|--------------|----------------------------------------------------------|
| `id`           | UUID string      | All messages | Stable identifier for this message                       |
| `role`         | `"user"` or `"assistant"` | All | Who authored the message                        |
| `content`      | string           | All messages | Markdown-formatted text content                          |
| `timestamp`    | ISO 8601 string  | All messages | When this message was written to storage                 |
| `tools_called` | array of strings | Assistant only | Tool names invoked during this turn. Absent on user messages. Empty array if no tools were called. |

**Filtering:** Internal Anthropic `tool_use` and `tool_result` message blocks are stripped before the response is serialized. Only `role: user` and `role: assistant` text messages are returned. Clients never see raw Anthropic API message formats.

**Response — 404 Not Found** — if `id` does not exist. See Section 4.

**Design rationale:** Returning `tools_called` on the message object (rather than only in the streaming `done` event) allows the UI to re-render tool badges correctly when it loads a historical conversation. Without this, tool call history is only visible in the live stream and is lost on page reload.

---

### 2.5 DELETE /api/v1/conversations/{id}

Permanently deletes a conversation and all its messages.

**Request**

```http
DELETE /api/v1/conversations/3fa85f64-5717-4562-b3fc-2c963f66afa6
Authorization: Bearer <key>
```

**Response — 204 No Content**

Empty body. Success.

**Response — 404 Not Found** — if `id` does not exist. See Section 4.

**Design rationale:** 204 with no body is the standard HTTP idiom for a successful delete — the resource no longer exists, so there is nothing to return. Returning 200 with a confirmation body introduces a parsing step that adds no value.

---

### 2.6 POST /api/v1/conversations/{id}/messages

Sends a user message and returns Claude's reply. This is the primary endpoint. It supports two response modes, negotiated by the `Accept` header.

#### 2.6.1 Default: Streaming Mode (SSE)

```http
POST /api/v1/conversations/3fa85f64-5717-4562-b3fc-2c963f66afa6/messages
Authorization: Bearer <key>
Content-Type: application/json
Accept: text/event-stream

{
  "message": "What bills are overdue?"
}
```

Response is `Content-Type: text/event-stream`. See Section 3 for the full event protocol.

#### 2.6.2 Non-Streaming Mode (JSON)

```http
POST /api/v1/conversations/3fa85f64-5717-4562-b3fc-2c963f66afa6/messages
Authorization: Bearer <key>
Content-Type: application/json
Accept: application/json

{
  "message": "What bills are overdue?"
}
```

**Response — 200 OK**

```json
{
  "conversation_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "message": {
    "id": "b2c3d4e5-f6a7-8901-bcde-f01234567890",
    "role": "assistant",
    "content": "You have **3 overdue bills** totalling $4,821.00:\n\n...",
    "timestamp": "2026-04-13T14:23:28.412Z",
    "tools_called": ["get_unpaid_bills"]
  }
}
```

**Request body — fields:**

| Field     | Type   | Required | Max length | Description              |
|-----------|--------|----------|------------|--------------------------|
| `message` | string | Yes      | 10,000 chars | The user's message text |

**Error responses:**

| Condition                          | HTTP Status | Error Code               |
|------------------------------------|-------------|--------------------------|
| `message` field absent or blank    | 400         | `EMPTY_MESSAGE`          |
| `message` exceeds 10,000 chars     | 400         | `MESSAGE_TOO_LONG`       |
| `{id}` not found                   | 404         | `CONVERSATION_NOT_FOUND` |
| Anthropic API error or timeout     | 502         | `UPSTREAM_AI_ERROR`      |
| QBO API error during tool call     | 502         | `UPSTREAM_QBO_ERROR`     |
| Any other internal failure         | 500         | `INTERNAL_ERROR`         |

For streaming mode, errors that occur after the stream has opened are delivered as SSE `error` events rather than HTTP status codes (see Section 3.5). HTTP-level errors (400, 404, 401) are always returned as HTTP responses before the stream opens.

**Design rationale:** The non-streaming path exists for CLI tools, integration tests, and clients in environments where SSE is inconvenient (e.g., some server-side rendering setups). Defaulting to streaming when no `Accept` header is sent is tempting but risks surprising non-browser clients. Instead, the client must explicitly opt in to SSE by sending `Accept: text/event-stream`, which is the correct HTTP content negotiation pattern.

---

### 2.7 GET /api/v1/health

Liveness probe. Returns immediately without checking external dependencies.

**Request**

```http
GET /api/v1/health
```

No auth required.

**Response — 200 OK**

```json
{
  "status": "ok"
}
```

---

### 2.8 GET /api/v1/ready

Readiness probe. Checks that all external dependencies are reachable before returning 200.

**Request**

```http
GET /api/v1/ready
```

No auth required.

**Response — 200 OK** (all dependencies healthy)

```json
{
  "status": "ready",
  "checks": {
    "anthropic": "ok",
    "qbo": "ok",
    "database": "ok"
  }
}
```

**Response — 503 Service Unavailable** (one or more dependencies unhealthy)

```json
{
  "status": "degraded",
  "checks": {
    "anthropic": "ok",
    "qbo": "error",
    "database": "ok"
  }
}
```

**Design rationale:** Separating liveness (`/health`) from readiness (`/ready`) follows Kubernetes probe conventions and is good practice even for non-Kubernetes deployments. A load balancer can keep routing to an instance that fails `/ready` (not ready for new traffic) while not restarting an instance that passes `/health` (the process is alive). The `checks` map identifies which dependency failed, enabling faster incident diagnosis.

---

## 3. SSE Streaming Event Format

The streaming response from `POST /api/v1/conversations/{id}/messages` uses the standard Server-Sent Events wire format:

```
event: <event_type>\n
data: <json_payload>\n
\n
```

Each event has exactly one `event:` line and one `data:` line. The `data:` value is always a valid JSON object. No multi-line data values.

### 3.1 Event Sequence

A complete turn produces events in this order:

```
event: tool_start     (zero or more, interleaved with other tool events)
event: tool_end       (one per tool_start)
event: token          (one or more, beginning of text output)
event: done           (exactly one, always last)
```

If an error occurs at any point after the stream opens:

```
event: error          (exactly one, replaces done)
```

A turn that requires no tool calls produces only `token` events followed by `done`.

### 3.2 tool_start

Emitted when Claude decides to invoke a QBO tool, before the tool call is dispatched.

```
event: tool_start
data: {"tool": "get_unpaid_bills"}
```

| Field  | Type   | Description                      |
|--------|--------|----------------------------------|
| `tool` | string | The QBO tool name being invoked  |

The frontend should use this to display a status message such as "Checking unpaid bills..." while the tool executes. The tool name maps to a human-readable label using the transform `get_unpaid_bills` → `unpaid bills` (strip `get_` prefix, replace underscores with spaces).

### 3.3 tool_end

Emitted when a tool call completes and its result has been returned to Claude.

```
event: tool_end
data: {"tool": "get_unpaid_bills", "summary": "Returned 4 unpaid bills totalling $8,200.00"}
```

| Field     | Type   | Description                                               |
|-----------|--------|-----------------------------------------------------------|
| `tool`    | string | The QBO tool name (matches the preceding `tool_start`)    |
| `summary` | string | A brief human-readable description of what was returned. Maximum 120 characters. Generated server-side, not from Claude. |

The frontend should use `tool_end` to clear the "Checking..." status and optionally show the badge (tool name) that will later appear on the completed message.

**Design rationale:** `summary` is generated server-side from the tool result (e.g., inspecting the count of returned records) rather than from Claude's interpretation. This keeps the intermediate display factual and avoids a second AI call. The summary is intentionally brief — it is a status indicator, not a data display.

### 3.4 token

Emitted for each text token streamed from Claude.

```
event: token
data: {"text": "You have "}
```

```
event: token
data: {"text": "**3 overdue bills**"}
```

| Field  | Type   | Description                                              |
|--------|--------|----------------------------------------------------------|
| `text` | string | A fragment of Claude's response text. Not guaranteed to be a complete word or sentence. May contain partial markdown. |

The frontend must accumulate `text` values and render the full concatenated string as markdown after the `done` event (or progressively, treating partial markdown gracefully).

**Design rationale:** Tokens are streamed as individual events rather than batched into sentences to minimise time-to-first-visible-text. Batching would reduce event count but defeats the purpose of streaming for the user. The frontend is responsible for progressive markdown rendering — this is a display concern, not an API concern.

### 3.5 done

Emitted exactly once, as the final event in a successful turn.

```
event: done
data: {
  "conversation_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "message_id": "b2c3d4e5-f6a7-8901-bcde-f01234567890",
  "tools_called": ["get_unpaid_bills"],
  "full_text": "You have **3 overdue bills** totalling $4,821.00:\n\n| Vendor | Due Date | Amount |\n..."
}
```

| Field             | Type             | Description                                                      |
|-------------------|------------------|------------------------------------------------------------------|
| `conversation_id` | UUID string      | The conversation this message belongs to                         |
| `message_id`      | UUID string      | The persisted ID of the assistant message now in storage         |
| `tools_called`    | array of strings | Ordered list of tool names invoked during this turn. Empty array if none. |
| `full_text`       | string           | The complete assistant text, identical to concatenating all `token.text` values. Provided as a convenience so the frontend does not need to maintain a running buffer. |

After receiving `done`, the frontend should:

1. Replace the streaming text display with the final rendered markdown from `full_text`.
2. Attach tool badges using `tools_called`.
3. Store `message_id` to reference this message (e.g., for future navigation to conversation history).
4. Re-enable the message input.

**Design rationale:** `full_text` is included in `done` even though it can be reconstructed from tokens, because event delivery is not guaranteed to be perfectly ordered or complete under poor network conditions. Providing the canonical full text in a single reliable terminal event means the client always has a fallback and does not need to handle token deduplication.

### 3.6 error

Emitted when an error occurs after the SSE stream has opened. Replaces `done`.

```
event: error
data: {"code": "UPSTREAM_QBO_ERROR", "message": "QuickBooks API is unavailable. Please try again shortly.", "recoverable": true}
```

| Field         | Type    | Description                                                           |
|---------------|---------|-----------------------------------------------------------------------|
| `code`        | string  | SCREAMING_SNAKE_CASE machine-readable code. Same codes as HTTP errors. |
| `message`     | string  | Human-readable description, safe to display in the UI.                |
| `recoverable` | boolean | True if the user can retry the same message. False if the conversation is corrupted or the error is permanent. |

When `recoverable` is true, the frontend should show a retry affordance (e.g., "Something went wrong — try again") and re-enable the input. When `recoverable` is false, the frontend should display a persistent error and prompt the user to start a new conversation.

### 3.7 Complete SSE Sequence Example

A full turn querying overdue bills:

```
event: tool_start
data: {"tool": "get_unpaid_bills"}

event: tool_end
data: {"tool": "get_unpaid_bills", "summary": "Returned 4 unpaid bills totalling $8,200.00"}

event: token
data: {"text": "You have "}

event: token
data: {"text": "**4 unpaid bills**"}

event: token
data: {"text": " totalling $8,200.00:\n\n"}

event: token
data: {"text": "| Vendor | Due Date | Amount |\n|---|---|---|\n"}

event: done
data: {"conversation_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "message_id": "b2c3d4e5-f6a7-8901-bcde-f01234567890", "tools_called": ["get_unpaid_bills"], "full_text": "You have **4 unpaid bills** totalling $8,200.00:\n\n| Vendor | Due Date | Amount |\n|---|---|---|\n"}
```

---

## 4. Error Handling

### 4.1 Error Response Envelope

Every 4xx and 5xx HTTP response uses this structure:

```json
{
  "error": {
    "code": "CONVERSATION_NOT_FOUND",
    "message": "No conversation with id 3fa85f64-5717-4562-b3fc-2c963f66afa6 exists.",
    "recoverable": true
  }
}
```

| Field         | Type    | Description                                                                |
|---------------|---------|----------------------------------------------------------------------------|
| `code`        | string  | SCREAMING_SNAKE_CASE. Stable across API versions. Use this in client logic. |
| `message`     | string  | Human-readable. May be shown directly in the UI. No stack traces or internal paths. |
| `recoverable` | boolean | True if a retry (with the same or corrected input) may succeed.            |

### 4.2 Error Code Reference

| HTTP Status | Code                      | Meaning                                              | Recoverable |
|-------------|---------------------------|------------------------------------------------------|-------------|
| 400         | `EMPTY_MESSAGE`           | `message` field is absent or empty string            | true        |
| 400         | `MESSAGE_TOO_LONG`        | `message` exceeds 10,000 characters                  | true        |
| 400         | `INVALID_CURSOR`          | `cursor` query parameter is malformed                | true        |
| 400         | `INVALID_LIMIT`           | `limit` is not a positive integer or exceeds 100     | true        |
| 401         | `UNAUTHORIZED`            | Missing or invalid Bearer token                      | false       |
| 404         | `CONVERSATION_NOT_FOUND`  | `{id}` does not correspond to any conversation       | true        |
| 502         | `UPSTREAM_AI_ERROR`       | Anthropic API returned an error or timed out         | true        |
| 502         | `UPSTREAM_QBO_ERROR`      | QuickBooks API returned an error or timed out        | true        |
| 500         | `INTERNAL_ERROR`          | Unexpected server-side failure                       | false       |

### 4.3 Recoverability Guide for Frontend Engineers

**recoverable: true** — The user or client can take action to fix the problem.

- Display the `message` text to the user.
- For `EMPTY_MESSAGE` and `MESSAGE_TOO_LONG`: surface inline validation, do not call the API.
- For `CONVERSATION_NOT_FOUND`: redirect to conversation list or offer to start a new conversation.
- For `UPSTREAM_AI_ERROR` and `UPSTREAM_QBO_ERROR`: show a retry button. The same message can be submitted again.

**recoverable: false** — The client cannot meaningfully proceed without user intervention.

- For `UNAUTHORIZED`: redirect to the login or API key configuration page.
- For `INTERNAL_ERROR`: show a generic error with a support contact. Do not offer retry.

### 4.4 What Errors Never Contain

Server errors must never include:

- Python stack traces
- Internal file paths
- Database query details
- Raw Anthropic or QBO API error bodies
- The `API_KEY` value
- QBO OAuth tokens

These are logged server-side only. The `message` field is written for humans, not for debugging.

---

## 5. Session and Authentication Flow

### 5.1 Overview

Phase 1 uses a single shared API key. There are no user accounts.

The frontend is expected to read the API key from a configuration source (environment variable, build-time constant, or a config file that is not committed to version control) and attach it to every request.

### 5.2 Header Format

```
Authorization: Bearer sk-finance-agent-abc123
```

The `Bearer` scheme keyword is required. A raw token with no scheme returns 401.

### 5.3 Token Validation Behaviour

| Condition                         | Response                 |
|-----------------------------------|--------------------------|
| Header absent                     | 401 `UNAUTHORIZED`       |
| Header present, wrong scheme      | 401 `UNAUTHORIZED`       |
| Header present, token does not match server key | 401 `UNAUTHORIZED` |
| Header present, token matches     | Request proceeds          |

All 401 responses use the same error body regardless of which condition triggered them. This prevents information leakage (an attacker cannot distinguish "header missing" from "wrong token"):

```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "A valid API key is required.",
    "recoverable": false
  }
}
```

### 5.4 Health and Readiness Endpoints

`GET /api/v1/health` and `GET /api/v1/ready` do not require authentication. This allows load balancers and orchestrators to probe health without holding an API key.

### 5.5 Security Constraints for the Frontend

- Never log the API key to the browser console.
- Never embed the API key in client-side JavaScript that is served publicly (i.e., do not inline it in HTML). Use environment variables at build time or a server-side proxy.
- Do not pass the API key as a URL query parameter (`?api_key=...`). Query parameters appear in server access logs and browser history.

---

## 6. Conversation State Model

### 6.1 Conversation Object

The canonical representation of a conversation as returned by `GET /api/v1/conversations/{id}`:

```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "created_at": "2026-04-13T14:23:01.412Z",
  "updated_at": "2026-04-13T14:45:22.100Z",
  "messages": [ /* array of message objects */ ]
}
```

`updated_at` is updated each time a message is appended (user or assistant). It is the sort key for `GET /api/v1/conversations`.

### 6.2 Message Object

```json
{
  "id": "b2c3d4e5-f6a7-8901-bcde-f01234567890",
  "role": "user",
  "content": "What bills are overdue?",
  "timestamp": "2026-04-13T14:23:15.000Z"
}
```

```json
{
  "id": "c3d4e5f6-a7b8-9012-cdef-012345678901",
  "role": "assistant",
  "content": "You have **3 overdue bills** totalling $4,821.00:\n\n...",
  "timestamp": "2026-04-13T14:23:28.412Z",
  "tools_called": ["get_unpaid_bills"]
}
```

`tools_called` is only present on assistant messages and will be an empty array `[]` if no tools were called. It is absent on user messages (not `null`, just absent).

### 6.3 What Is Not Exposed

The internal Anthropic conversation history stored in the database contains `tool_use` and `tool_result` message blocks. These are implementation details of the Claude API interaction loop. They are stripped at the serialization boundary. Clients see only user and assistant text messages.

This means a conversation with the following internal Anthropic message sequence:

```
[user text] → [assistant tool_use] → [user tool_result] → [assistant tool_use] → [user tool_result] → [assistant text]
```

Is returned to the client as:

```
[user text] → [assistant text, tools_called: ["tool_a", "tool_b"]]
```

### 6.4 Conversation List Item Object

A lighter object used in `GET /api/v1/conversations` list responses. Does not include `messages`.

```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "created_at": "2026-04-13T14:23:01.412Z",
  "updated_at": "2026-04-13T14:45:22.100Z",
  "preview": "What bills are overdue?"
}
```

`preview` is the first 100 characters of the first user message, plain text (no markdown). Empty string if the conversation has no user messages yet.

---

## 7. QBO Tool Set

For reference, the 13 tools exposed to Claude in this API service are listed below. These match `src/qbo_mcp_server.py` exactly. The frontend does not call these directly — they are called server-side by Claude. They appear in `tools_called` fields and SSE `tool_start`/`tool_end` events.

**Read-only tools (10):**

| Tool name              | What it fetches                            |
|------------------------|--------------------------------------------|
| `get_company_info`     | Company name, address, contact details     |
| `get_vendors`          | Vendor/supplier list                       |
| `get_bills`            | All bills (accounts payable)               |
| `get_unpaid_bills`     | Unpaid bills only (balance > 0)            |
| `get_bill_payments`    | Historical payment records                 |
| `get_accounts`         | Chart of accounts (filterable by type)     |
| `get_invoices`         | Invoices sent to customers (AR)            |
| `get_customers`        | Customer list                              |
| `get_profit_and_loss`  | P&L report                                 |
| `get_balance_sheet`    | Balance sheet report                       |

**Write tools — bill payment flow (3):**

| Tool name              | What it does                                           |
|------------------------|--------------------------------------------------------|
| `get_bill_by_id`       | Fetch a single bill by its QBO ID                      |
| `preview_bill_payment` | Validate a payment and return a confirmation token     |
| `create_bill_payment`  | Execute a payment using a confirmation token           |

The write tool flow is always: `preview_bill_payment` → user confirmation → `create_bill_payment`. Claude enforces this via its system prompt. The API does not enforce this at the HTTP level — it is a Claude-level constraint. The frontend should display tool events (`tool_start`/`tool_end`) for all three write tools and surface the preview details when `preview_bill_payment` appears in `tools_called`.

---

## 8. Design Decisions and Rationale

### 8.1 SSE over WebSockets

SSE is one-directional (server to client) and is the correct fit here. The client sends a single HTTP POST and the server streams back a response. WebSockets are bidirectional and require a persistent handshake-upgraded connection — that overhead is unnecessary when the communication pattern is strictly request-response with a streaming reply. SSE also works natively with `fetch` + `EventSource` in all modern browsers without additional libraries, and reconnects automatically on network drop.

### 8.2 Content Negotiation for Streaming

Using `Accept: text/event-stream` to opt into SSE follows the HTTP specification's content negotiation mechanism. The alternative (a separate `?stream=true` query parameter) is non-standard and is a dark pattern in REST APIs. Content negotiation also allows proxies and gateways to make routing decisions based on standard headers.

### 8.3 Cursor-based Pagination

Offset-based pagination (`?page=2`) is simple to implement but breaks when items are inserted or change their sort order between requests (e.g., a new message updates `updated_at`, shifting a conversation from page 3 to page 1). Cursor-based pagination anchors the page boundary to a specific record, giving stable results across pages. The cursor is opaque — clients treat it as a black box — which gives the backend freedom to change the cursor encoding without a client-side change.

### 8.4 Stripping Tool-Use Messages from the Client API

The Anthropic message history format includes `tool_use` and `tool_result` blocks that are internal bookkeeping for the Claude API's tool-calling loop. Exposing these to the frontend would couple the frontend to Anthropic API implementation details. If Anthropic changes their message format (which they have done in the past), every frontend client breaks. Stripping at the serialization boundary insulates the frontend from this risk and produces a cleaner message model.

### 8.5 full_text in the done Event

Including the complete response text in `done` is redundant if every `token` event was received in order. However, SSE delivery guarantees are weaker than WebSocket delivery. Under poor network conditions, tokens can be dropped or received out of order before a reconnect. `full_text` in `done` is the authoritative canonical text. Clients should display the `full_text` value after receiving `done`, not the accumulated token buffer, to guarantee correct final rendering.

### 8.6 message_id in the done Event

The `message_id` in the `done` event is the UUID that was assigned when the assistant message was persisted to the database. Providing it in `done` means the frontend can reference this specific message in subsequent requests (e.g., loading conversation history and matching streamed messages to stored messages without a round-trip to `GET /api/v1/conversations/{id}`).

### 8.7 tools_called on Stored Messages

Storing `tools_called` on the persisted message object (and returning it in `GET /api/v1/conversations/{id}`) allows the UI to show tool badges on historical messages. Without this, tool call information only exists in the live SSE stream and is lost when the page is refreshed. This is a deliberate denormalization chosen for read performance and frontend simplicity.

### 8.8 Bearer Token Authentication (Phase 1)

A single shared Bearer token is a deliberate Phase 1 simplification. It provides a meaningful security boundary (unauthenticated requests are rejected) without requiring a full identity system. The header-based approach matches what the future OAuth2 implementation will also use (`Authorization: Bearer <jwt>`), so the client-side auth header code does not need to change when authentication is upgraded in a later phase.

### 8.9 The /api/v1 Prefix

Versioning in the URL path is the most visible and least ambiguous approach for a team building against a spec document. Header-based versioning (`Accept: application/vnd.finance-agent.v1+json`) is more technically correct per REST conventions but is significantly harder to test, log, and communicate to frontend developers. Path-based versioning was chosen because developer clarity is more valuable than RFC purity for an internal single-tenant API.

---

## 9. Frontend Integration Checklist

A new frontend engineer implementing against this spec should be able to verify the following within their first working session:

- [ ] `POST /api/v1/conversations` returns 201 with a UUID `id`
- [ ] `GET /api/v1/conversations/{id}` returns 200 with empty `messages` array
- [ ] `POST /api/v1/conversations/{id}/messages` with `Accept: text/event-stream` opens a stream and delivers `token` events
- [ ] The stream terminates with a `done` event containing `full_text`
- [ ] `GET /api/v1/conversations/{id}` after sending a message returns both user and assistant messages
- [ ] `DELETE /api/v1/conversations/{id}` returns 204 and `GET` on the same ID returns 404
- [ ] A request without an `Authorization` header returns 401
- [ ] A request to a missing conversation ID returns 404 with `code: "CONVERSATION_NOT_FOUND"`
- [ ] `GET /api/v1/conversations` returns an empty `data` array when no conversations exist
- [ ] `GET /api/v1/health` returns 200 without an auth header
