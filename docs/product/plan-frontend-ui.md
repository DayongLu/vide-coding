# Implementation Plan: Frontend Web UI

**Status:** Draft
**Date:** 2026-04-13
**Author:** Tech Lead
**API Contract:** `docs/design/ux-backend-api.md`
**Backend Plan:** `docs/product/plan-backend-api.md`
**Target branch:** `feature/frontend-ui`

---

## 1. Tech Stack Decision

### Decision: Vanilla HTML + CSS + JavaScript (no framework)

**Options evaluated:**

**Option A — Vanilla HTML/JS/CSS**
- Zero build step. Edit a file, refresh the browser.
- No `node_modules`, no bundler config, no transpilation, no version matrix.
- `EventSource` and `fetch` are native browser APIs — the two features this UI depends on most heavily are built in.
- The `marked.js` library (already in use in `index.html`) handles markdown rendering as a single CDN script tag. No installation required.
- A skilled developer can read and modify the entire UI in one sitting.

**Option B — Alpine.js**
- A reactive declarative model (x-data, x-on, x-show) is genuinely useful for the confirmation card toggle and stream state management.
- Adds approximately 15 KB of CDN script, one more dependency to track.
- The reactivity benefit is real but not large enough to justify the cognitive overhead for a single-developer internal tool.
- Alpine's syntax is unfamiliar to developers coming from standard JS; it is another thing to learn.

**Option C — htmx**
- Well-suited to hypermedia-driven server interactions but poorly suited to SSE progressive rendering. htmx's SSE extension appends whole HTML fragments — it cannot accumulate partial token events into a growing text buffer.
- The SSE token accumulation pattern (appending `text` fragments, replacing the message with `full_text` at `done`) requires explicit JS logic that htmx cannot encapsulate cleanly.
- Forces the server to produce HTML fragments, which conflicts with the JSON API contract.

**Option D — Preact / React**
- Component model and virtual DOM are appropriate for an app with 5+ developers and a design system. For a single-developer internal tool, the build pipeline (Vite or CRA), JSX compilation, and module bundling are pure overhead.
- The complexity cost arrives on day one; the benefits scale with team size and component reuse — neither of which applies here.

**Verdict: Vanilla HTML/JS/CSS.**

The UI has three interacting pieces of state: the conversation list, the active message stream, and the payment confirmation card. This is comfortably within what vanilla JS can manage with a small explicit state object. The engineering cost of maintaining a build pipeline and framework version on a solo-developer internal tool exceeds the benefit. If this becomes a team product with a design system, Preact is the right next step. That migration is straightforward from a clean vanilla baseline.

**Libraries to include (CDN only, no npm):**

| Library | Version | Purpose |
|---|---|---|
| `marked.js` | latest stable | Markdown rendering (already present) |

No other external libraries. The `EventSource` API, `fetch`, and `CSS Grid`/`Flexbox` are sufficient for everything else.

---

## 2. File Structure

The frontend stays within the existing Flask/FastAPI serving model. Static assets live in `src/static/` (new directory). The single HTML entrypoint stays in `src/templates/`. The FastAPI app serves the template via a `GET /` route and exposes `/static/` for CSS and JS files.

```
src/
├── templates/
│   └── index.html              # Entrypoint; imports CSS and JS from /static/
├── static/
│   ├── css/
│   │   └── app.css             # All styles extracted from the current inline <style> block
│   └── js/
│       ├── api.js              # API client module: fetch wrappers for all 7 endpoints
│       ├── sse.js              # SSE connection manager: open, parse events, close
│       ├── state.js            # Application state: conversations list, active conversation, stream state
│       ├── renderer.js         # DOM rendering functions: message bubbles, tool badges, payment card
│       └── app.js              # Entry point: wires event listeners, orchestrates modules
```

**Why not a single `app.js`?**

The existing `index.html` has ~100 lines of inline JavaScript. The new UI will be significantly larger — SSE handling, payment confirmation, conversation list management, error display. A single file becomes unnavigable past ~300 lines. The module split follows single-responsibility: each file has one job and can be read and debugged independently. There is no build step — the browser loads each file as a `<script type="module">` tag, which is supported by all modern browsers and works with no tooling.

**Serving static files:**

FastAPI serves `src/static/` via `StaticFiles` mount. A `GET /` route renders `src/templates/index.html`. The existing Flask `app.py` also serves the template; it will need `url_for('static', ...)` references updated. Since `app.py` is a dev shortcut and will be superseded by the FastAPI service, only the FastAPI service path is required to be correct.

**The existing `index.html` is rewritten, not extended.** The current file is a single-page app tightly coupled to the `/chat` endpoint. The new UI is a different application. Attempting to extend the existing file incrementally would produce an unmaintainable hybrid. A clean rewrite against the new API contract is the correct approach.

---

## 3. Component Breakdown

The UI has five logical components. These are not framework components — they are conceptual boundaries that map to functions and DOM regions.

### 3.1 Sidebar — Conversation List

**DOM region:** A fixed-width left panel (`<aside>`).

**Responsibilities:**
- Display a scrollable list of conversations, newest first, with preview text and relative timestamp (e.g., "2 hours ago").
- Highlight the active conversation.
- Expose a "New conversation" button that calls `POST /api/v1/conversations` and activates the new conversation.
- Load more conversations when the user scrolls to the bottom (infinite scroll via cursor pagination).
- Delete a conversation via a trash icon (with a confirmation prompt using `window.confirm`).

**Data source:** `GET /api/v1/conversations` (paginated). The sidebar fetches the list on page load and appends when the user scrolls.

**Interaction with Chat Area:** Clicking a conversation item calls `GET /api/v1/conversations/{id}`, hydrates the chat area with stored messages, and updates `state.activeConversationId`.

### 3.2 Chat Area — Message Thread

**DOM region:** The main content panel (right of sidebar).

**Responsibilities:**
- Render existing messages on conversation load (user and assistant bubbles, tool badges on assistant messages).
- Show a welcome / empty state when no conversation is active.
- Append the user's sent message immediately (optimistic render) before the SSE stream opens.
- Display the streaming assistant message bubble with a cursor animation while tokens arrive.
- Replace the streaming bubble with the final rendered markdown once `done` is received.
- Attach tool badges to the completed message.
- Auto-scroll to the bottom as tokens arrive, unless the user has manually scrolled up (in which case, do not force-scroll).

### 3.3 Tool Status Bar

**DOM region:** A transient status strip that appears inside the assistant message bubble while a tool call is in progress, between `tool_start` and `tool_end` events.

**Responsibilities:**
- On `tool_start`: show "Checking [human label]..." with a spinner.
- On `tool_end`: replace the spinner with a checkmark and show the `summary` text for 1.5 seconds before fading out.
- Multiple sequential tools: each `tool_start`/`tool_end` pair updates the same strip. They do not stack.

**Tool name to human label mapping:** `get_unpaid_bills` → `unpaid bills` (strip `get_` prefix, replace underscores with spaces). This matches the UX spec's suggested transform exactly.

### 3.4 Payment Confirmation Card

**DOM region:** A card that appears inline in the chat area, below the assistant message that contains the `preview_bill_payment` response. It is not a modal.

**Responsibilities:**
- Parse the assistant's `full_text` from the `done` event to detect whether a bill payment preview was included in this turn (by checking `tools_called` for `preview_bill_payment`).
- If a preview was included, render a structured card below the message bubble showing: vendor name, amount, bank account, and the confirmation prompt.
- Expose two buttons: "Confirm Payment" and "Cancel".
- On "Confirm Payment": send a follow-up user message "Confirm payment" to `POST /api/v1/conversations/{id}/messages` and open a new SSE stream for Claude's response. The card is replaced by the new assistant message.
- On "Cancel": send a follow-up user message "Cancel payment" to the API. The card is dismissed and Claude's cancellation response streams in normally.

**Design rationale:** The confirmation flow is entirely message-driven. The frontend does not directly call `create_bill_payment` or manage `confirmation_token` values. That is Claude's responsibility (via the system prompt's two-step rule). The UI's job is to make the "Confirm" and "Cancel" intents obvious and to prevent the user from typing a free-form message during the confirmation window. When the confirmation card is visible, the text input is disabled.

**Detection heuristic:** A payment preview turn is identified by `tools_called` in the `done` event containing `preview_bill_payment`. The card content is extracted from the `full_text` using a lightweight text parse (look for the preview block structure Claude produces). This is pragmatic: the server does not send a separate structured preview event, so the frontend must read the natural-language response. If the parse fails, the card is not shown and the user can type a confirmation manually — a safe fallback.

**Structured card alternative (if parse is unreliable):** The backend plan notes that `done` event metadata could be extended. If the text parsing approach proves brittle in practice, the server-side `done` event payload should be extended with a `payment_preview` field containing structured data. This is deferred — start with the text-parsing approach and escalate if it fails in testing.

### 3.5 Input Area

**DOM region:** Fixed bottom bar with a textarea and a send button.

**Responsibilities:**
- Accept multi-line input via a `<textarea>` that grows vertically up to 5 lines.
- Send on `Enter` (submit); `Shift+Enter` inserts a newline.
- Disable during in-flight SSE stream and during payment confirmation card display.
- Validate client-side: block send if the message is empty or exceeds 10,000 characters. Show an inline character count when within 500 characters of the limit.
- Show an error inline (below the textarea) for recoverable API errors, with a retry affordance.

---

## 4. SSE Handling

All SSE logic is isolated in `src/static/js/sse.js`. This module is the only place in the codebase that reads SSE events.

### 4.1 Why `fetch` + `ReadableStream`, not `EventSource`

`EventSource` is the browser's built-in SSE API. It has one critical limitation: it only supports `GET` requests. Sending a message via SSE requires a `POST` (with a JSON body containing the message text). This eliminates `EventSource` for the streaming message endpoint.

The correct approach is `fetch()` with a streaming response body: open the request, then read the response as a `ReadableStream`, decode lines, and parse SSE events manually. This is ~40 lines of code and is the standard pattern for POST-initiated SSE in modern browsers.

### 4.2 SSE Line Parsing

The wire format is:
```
event: <type>\n
data: <json>\n
\n
```

The parser maintains a two-variable buffer (`currentEvent`, `currentData`). On each line:
- Lines starting with `event:` set `currentEvent`.
- Lines starting with `data:` set `currentData`.
- Blank lines dispatch `{ type: currentEvent, data: JSON.parse(currentData) }` to the event handler, then reset the buffer.

This parsing is deliberately simple. The API contract guarantees exactly one `event:` line and one `data:` line per event, and no multi-line data values. The parser does not need to handle `id:`, `retry:`, or multi-line `data:` (which are valid SSE features but not used by this API).

### 4.3 Event Handler Dispatch

`sse.js` exports one function:

```
openStream(conversationId, message, handlers)
```

Where `handlers` is an object with optional callbacks: `onToken`, `onToolStart`, `onToolEnd`, `onDone`, `onError`. Each callback receives the parsed event data payload. `app.js` calls `openStream` and supplies the handlers; DOM manipulation lives in `renderer.js`, not in `sse.js`.

### 4.4 Connection Lifecycle

1. `openStream` is called when the user submits a message.
2. The `fetch` call opens. The response body `ReadableStream` is read in a loop with a `TextDecoder`.
3. While streaming: the UI disables the input and the send button.
4. On `done` or `error`: the reader loop exits, the stream is closed, the UI re-enables the input.
5. On network error (fetch rejects or the reader throws): treat as a recoverable error, display an inline retry message.
6. Abort: if the user navigates away or switches conversations mid-stream, the fetch is cancelled via `AbortController`. The partial message is discarded.

### 4.5 Progressive Markdown Rendering

Accumulate token text in a string buffer as `token` events arrive. Update the assistant message bubble's `innerHTML` with `marked.parse(buffer)` on each token event. `marked.js` handles partial markdown gracefully — a partial table row renders as plain text until the closing `|` arrives. This is acceptable for an internal tool.

After `done`, replace the buffer with `full_text` from the `done` event and re-render. This is the canonical text and overrides any minor rendering artifacts from partial accumulation.

---

## 5. State Management

All mutable state lives in `src/static/js/state.js` as a plain module-level object. There is no reactive framework. When state changes, the caller explicitly invokes the appropriate renderer function.

```
state = {
  conversations: [],          // ConversationListItem[] from GET /api/v1/conversations
  nextCursor: null,           // Pagination cursor for the next page of conversations
  hasMore: false,             // Whether more conversations exist past the current page
  activeConversationId: null, // UUID of the conversation currently displayed
  messages: [],               // MessageObject[] for the active conversation (user + assistant only)
  streamState: {
    active: false,            // Whether an SSE stream is currently open
    buffer: '',               // Accumulated token text for the in-progress assistant message
    toolsCalledThisTurn: [],  // Tool names seen in tool_start events this turn
    currentTool: null,        // Tool name currently being executed (between tool_start and tool_end)
  },
  paymentPending: false,      // Whether the payment confirmation card is visible
  error: null,                // { message, recoverable } or null
}
```

**State transitions are explicit and synchronous.** There is no event bus. When `app.js` receives an SSE event, it:
1. Updates `state` directly.
2. Calls the relevant `renderer.js` function with the new state or the specific changed value.

This is a deliberate choice over a reactive model. The state shape is small and the transitions are few enough that explicit calls are easier to follow than reactive bindings, especially for debugging SSE timing issues.

**No localStorage persistence.** The conversation list is loaded from the API on page load. This means a page refresh re-fetches the list (correct behaviour, since the server is the source of truth). Active conversation is not preserved across reloads — the user lands on the empty state and clicks a conversation from the sidebar. This is acceptable for Phase 1.

---

## 6. Bill Payment Confirmation UI

### 6.1 Detection

When a `done` event arrives with `tools_called` containing `preview_bill_payment`, the payment confirmation card flow begins. The `full_text` is inspected for a preview block.

Claude's system prompt mandates that after `preview_bill_payment`, it presents the payment details in a structured format before asking for confirmation. The frontend detects this block by looking for specific markers in the text (e.g., the presence of "Vendor:", "Amount:", and "Confirm" in proximity). If the block is found, the card is populated with extracted values. If not found, the card is not shown and the user can type a confirmation message manually.

This detection heuristic is brittle by nature. The risk is acceptable for Phase 1 because: (a) Claude's output is consistent when given a well-constrained system prompt, and (b) the fallback (manual confirmation by typing) is safe. Log a console warning when `preview_bill_payment` is in `tools_called` but the card cannot be extracted, so it is visible during testing.

### 6.2 Card Layout

The confirmation card appears as a distinct visual element below the assistant's preview message. It uses a left border accent in amber to signal that action is required. It is not a modal — the user can still scroll the conversation while the card is visible.

**Fields displayed:**
- Vendor name
- Bill ID (for reference)
- Payment amount (formatted as USD currency)
- Bank account name
- Payment date

**Actions:**
- "Confirm Payment" button (primary, green) — sends the follow-up message.
- "Cancel" button (secondary, outlined) — sends a cancellation message.

**Input lockout:** While the card is visible, `state.paymentPending = true`. The text input and send button are disabled with a visible tooltip "Confirm or cancel the pending payment first."

### 6.3 Confirm Flow

1. User clicks "Confirm Payment".
2. Card is immediately removed from the DOM. The user message "Confirm payment" is appended to the chat thread.
3. `POST /api/v1/conversations/{id}/messages` is called with `{ "message": "Confirm payment" }`.
4. SSE stream opens. Claude invokes `create_bill_payment` with the stored token — `tool_start` and `tool_end` events appear for `create_bill_payment`.
5. Claude's success response streams in as token events, then `done`.
6. `state.paymentPending = false`. Input re-enabled.

### 6.4 Cancel Flow

1. User clicks "Cancel".
2. Card is immediately removed. User message "Cancel payment" is appended.
3. `POST /api/v1/conversations/{id}/messages` with `{ "message": "Cancel payment" }`.
4. Claude's acknowledgement streams in normally.
5. Input re-enabled.

### 6.5 Timeout Handling

Payment tokens expire after 5 minutes (enforced server-side). The frontend does not implement a countdown timer in Phase 1. If the token has expired by the time the user confirms, Claude will receive a `TOKEN_NOT_FOUND` or `TOKEN_EXPIRED` error from `create_bill_payment` and will explain this in its response. The frontend treats this as a normal assistant message. The user can run `preview_bill_payment` again.

Displaying a countdown timer is explicitly deferred — it requires the `done` event to include a `token_expires_at` field, which the backend plan notes as a possible future extension.

---

## 7. Task Breakdown

Tasks are ordered by dependency. Each task maps to one logical commit. Complexity: S = a few hours, M = half a day, L = full day.

### Sprint 1 — Scaffold and Layout

#### Task F1 — Create `src/static/` directory structure and extract CSS [S]

**Done criteria:**
- `src/static/css/app.css` created with all styles from the current `index.html` `<style>` block extracted verbatim.
- `src/static/js/api.js`, `sse.js`, `state.js`, `renderer.js`, `app.js` created as empty module stubs (just `// TODO` comments and `export` placeholders).
- `src/templates/index.html` rewritten as a structural HTML skeleton: sidebar placeholder, chat area placeholder, input area. Imports CSS and JS modules. Does not contain inline styles or JS.
- FastAPI `main.py` (from the backend plan) mounts `src/static/` at `/static/`. `GET /` route renders `index.html` via Jinja2 templates.
- Page loads without JS errors (empty state, no data).

**Files changed:** `src/static/css/app.css` (new), `src/static/js/*.js` (5 new stubs), `src/templates/index.html` (rewrite), `src/api/main.py` (add static mount and root route)

**Dependency:** Backend Task 10 (app factory) must exist for the static mount. Task F1 can proceed in parallel if the developer stubs out `main.py` temporarily.

#### Task F2 — Two-column layout: sidebar + chat area [S]

**Done criteria:**
- `app.css` implements the two-column layout: fixed 260px sidebar, flexible-width chat area, using CSS Grid on `<body>` or a wrapper `<div>`.
- Sidebar has a "New conversation" button and an empty conversation list `<ul>`.
- Chat area has a welcome empty state (matching the current `index.html` welcome section).
- Input area is fixed at the bottom of the chat column.
- Layout is visually correct at viewport widths 1024px–1920px (internal tool; no mobile requirement in Phase 1).

**Files changed:** `src/static/css/app.css`, `src/templates/index.html`

#### Task F3 — API client module `api.js` [S]

**Done criteria:**
- `api.js` exports typed wrapper functions for all 7 endpoints:
  - `createConversation() → Promise<ConversationResponse>`
  - `listConversations(cursor?) → Promise<ConversationListResponse>`
  - `getConversation(id) → Promise<ConversationResponse>`
  - `deleteConversation(id) → Promise<void>`
  - `sendMessageJson(id, message) → Promise<SendMessageResponse>` (non-streaming, for testing)
  - `checkHealth() → Promise<{status}>` (used on startup to verify API reachability)
- Each function reads the API key from a module-level constant (`const API_KEY = window.FINANCE_AGENT_API_KEY`). The key is injected by the server into the HTML template via a `<script>` tag that sets `window.FINANCE_AGENT_API_KEY` from a server-side env var — this avoids hardcoding it in a static file.
- All functions throw a structured error object `{ code, message, recoverable }` on non-2xx responses, parsed from the error envelope.
- No SSE calls in this module. SSE is handled by `sse.js`.

**Files changed:** `src/static/js/api.js`

**Security note:** The API key is passed from the server into the page via a Jinja2 template variable, not hardcoded in a static file. This is appropriate for an internal tool where the server and client are on the same trusted host. The key must not appear in the static JS files themselves, which would commit it to version control.

#### Task F4 — State module `state.js` [S]

**Done criteria:**
- `state.js` exports the `state` object (as documented in Section 5) and a set of mutator functions: `setActiveConversation(id)`, `setConversations(list)`, `appendConversation(conv)`, `removeConversation(id)`, `beginStream()`, `appendToken(text)`, `endStream(donePayload)`, `setPaymentPending(bool)`, `setError(err)`, `clearError()`.
- Mutator functions update `state` and return the new value. They do not touch the DOM.
- Module has no external imports.

**Files changed:** `src/static/js/state.js`

#### Task F5 — Renderer module `renderer.js` [M]

**Done criteria:**
- `renderer.js` exports functions that create or update DOM elements:
  - `renderConversationList(conversations, activeId)` — populates the sidebar `<ul>`.
  - `renderConversationItem(conv, isActive)` — creates one `<li>` element.
  - `renderMessage(message)` — creates a message bubble element (user or assistant). Applies `marked.parse()` for assistant messages. Attaches tool badges from `tools_called`.
  - `renderStreamingMessage()` — creates the in-progress assistant bubble with a cursor animation. Returns the element so the caller can hold a reference.
  - `updateStreamingMessage(element, buffer)` — updates the bubble's innerHTML with `marked.parse(buffer)`.
  - `finalizeStreamingMessage(element, donePayload)` — replaces the bubble content with `marked.parse(donePayload.full_text)`, attaches tool badges.
  - `renderToolStatus(toolName)` — shows the tool status strip. Returns the element.
  - `updateToolStatus(element, summary)` — transitions from spinner to checkmark with the summary text. Fades out after 1.5s.
  - `renderPaymentCard(previewData)` — builds the payment confirmation card element.
  - `renderError(err)` — shows inline error below the input.
  - `clearError()` — removes the inline error.
  - `scrollToBottom(smooth)` — scrolls the chat area to the bottom if the user is not manually scrolled up.
- No API calls or state mutations in this module. Pure DOM construction and updates.

**Files changed:** `src/static/js/renderer.js`

#### Task F6 — SSE module `sse.js` [M]

**Done criteria:**
- `sse.js` exports `openStream(conversationId, message, handlers, signal)`.
- Uses `fetch` with the `Authorization` header and `Accept: text/event-stream`.
- Reads the response body as a `ReadableStream` with `TextDecoder`.
- Parses SSE lines into `{ type, data }` events as described in Section 4.2.
- Dispatches parsed events to the appropriate handler in `handlers`.
- `signal` is an `AbortController` signal; aborting the signal stops reading and closes the stream cleanly.
- On fetch error or read error, calls `handlers.onError({ code: 'NETWORK_ERROR', message: 'Connection lost. Please try again.', recoverable: true })`.
- Exported from the module. No DOM interaction, no state mutation.
- Manual test: opening a stream against the live API delivers token events and a `done` event.

**Files changed:** `src/static/js/sse.js`

### Sprint 2 — Wiring and Core Flows

#### Task F7 — Conversation list: load, select, create, delete [M]

**Done criteria:**
- On `app.js` initialization: `listConversations()` is called; results passed to `state.setConversations()` and `renderer.renderConversationList()`.
- Clicking a conversation item: `getConversation(id)` is called; messages passed to `state.setActiveConversation()` and rendered in the chat area.
- Clicking "New conversation": `createConversation()` is called; the new conversation is prepended to the sidebar list and activated (empty chat area, no messages).
- Clicking the delete icon on a conversation item: `window.confirm()` prompt, then `deleteConversation(id)`. The item is removed from the sidebar. If the deleted conversation was active, the chat area shows the empty state.
- Infinite scroll: an `IntersectionObserver` on the last sidebar `<li>` triggers `listConversations(state.nextCursor)` when `state.hasMore` is true. New results are appended to the list.
- Error handling: if `listConversations()` fails, a non-blocking error banner appears at the top of the sidebar ("Could not load conversations. Refresh to retry.").

**Files changed:** `src/static/js/app.js`

#### Task F8 — Send message and stream response [M]

**Done criteria:**
- Send button and Enter key call `sendMessage()` in `app.js`.
- `sendMessage()` validates the input (non-empty, <=10,000 chars). Shows an inline validation error if invalid.
- If no active conversation exists, `createConversation()` is called first, the new conversation is activated, and the message is sent.
- The user message is immediately rendered in the chat area (optimistic).
- `state.beginStream()` is called; input and send button are disabled.
- `openStream()` is called with the conversation ID and message.
- On `token`: `state.appendToken(text)` then `renderer.updateStreamingMessage(element, state.streamState.buffer)`.
- On `tool_start`: `renderer.renderToolStatus(toolName)` replaces the typing indicator.
- On `tool_end`: `renderer.updateToolStatus(element, summary)`.
- On `done`: `state.endStream(payload)` then `renderer.finalizeStreamingMessage(element, payload)`. Input re-enabled.
- On `error`: render the SSE error as an inline message. Input re-enabled. Show a retry affordance.
- Auto-scroll behaviour: scroll to bottom on each token unless the user has manually scrolled up by more than 100px (checked via `chatArea.scrollTop + chatArea.clientHeight < chatArea.scrollHeight - 100`).

**Files changed:** `src/static/js/app.js`

#### Task F9 — Bill payment confirmation card [M]

**Done criteria:**
- After `done` event is processed: if `tools_called` includes `preview_bill_payment`, `detectPaymentPreview(fullText)` is called.
- `detectPaymentPreview` is a function in `renderer.js` that searches the text for payment preview markers and returns a structured object `{ vendor, amount, accountName, billId, date }` or `null`.
- If a preview is detected: `renderer.renderPaymentCard(previewData)` inserts the card below the assistant message. `state.setPaymentPending(true)`. Input is disabled.
- Confirm button click: card removed, user message "Confirm payment" rendered optimistically, `openStream()` called with the confirmation message.
- Cancel button click: card removed, user message "Cancel payment" rendered, `openStream()` called with the cancellation message.
- If `detectPaymentPreview` returns null (parse failure): card is not shown. A `console.warn` is emitted. The user can type a confirmation manually (the input remains enabled after `done`).
- Integration test: simulate a `done` event with `preview_bill_payment` in `tools_called` and a realistic `full_text`. Verify the card renders.

**Files changed:** `src/static/js/renderer.js` (add `detectPaymentPreview`, `renderPaymentCard`), `src/static/js/app.js`

#### Task F10 — Error handling: HTTP errors and recoverable states [S]

**Done criteria:**
- All `api.js` error paths render `renderer.renderError(err)` in the input area.
- `UNAUTHORIZED` (recoverable: false): full-page error overlay with "Invalid API key. Contact your administrator." No retry affordance.
- `CONVERSATION_NOT_FOUND` (recoverable: true): inline error "This conversation no longer exists." with a "Start a new conversation" link.
- `UPSTREAM_AI_ERROR` / `UPSTREAM_QBO_ERROR` (recoverable: true): inline error with a "Try again" button that resubmits the last message.
- `INTERNAL_ERROR` (recoverable: false): inline error "Something went wrong. Please try again or start a new conversation." No retry.
- Network error (fetch failed): inline error with retry affordance.
- All errors clear when the user sends a new message or clicks a retry button.

**Files changed:** `src/static/js/app.js`, `src/static/js/renderer.js`

#### Task F11 — Polish: empty states, loading indicators, sidebar timestamp [S]

**Done criteria:**
- Welcome empty state in the chat area matches the current design (suggestion chips for common queries). Clicking a chip populates the input and triggers send.
- The sidebar shows a skeleton loading state (grey animated bars) while `listConversations()` is in flight.
- Conversation timestamps in the sidebar are relative ("2 hours ago", "Yesterday") using a simple `formatRelativeTime` helper in `renderer.js`. No external library.
- The "New conversation" button in the sidebar shows a spinner while `createConversation()` is in flight.
- The send button shows a spinner (CSS only) while a stream is active, instead of just being disabled.
- Tool badge transform: `get_unpaid_bills` → `unpaid bills` is applied consistently in `renderer.js`.

**Files changed:** `src/static/css/app.css`, `src/static/js/renderer.js`, `src/static/js/app.js`

---

## 8. What to Defer

These items are explicitly out of scope for Phase 1. They are documented here to prevent scope creep.

| Item | Reason to defer | Future trigger |
|---|---|---|
| **Full markdown rendering edge cases** | `marked.js` covers 95% of what Claude produces. Edge cases (nested tables, footnotes) are rare in financial responses. | Defer until a specific rendering bug is reported. |
| **Dark mode** | CSS custom properties are the right approach; not worth the setup cost for a v1 internal tool. | Defer until UX spec includes a dark mode design. |
| **Mobile / responsive layout** | This is an internal desktop tool. The two-column layout requires at least 800px. | Defer until there is a stated requirement for mobile access. |
| **Markdown code block copy button** | Claude rarely produces code blocks in financial responses. | Defer. |
| **Payment confirmation countdown timer** | Requires the backend `done` event to include `token_expires_at`. Backend defers this. | Implement alongside the backend extension in a future sprint. |
| **Conversation search / filter** | The sidebar is a plain chronological list. | Defer until the conversation list grows large enough to need it. |
| **Message edit / regenerate** | Requires the API to support message patching or conversation rollback. Not in the backend plan. | Defer. |
| **File upload / attachment** | Not in scope for this tool. QBO data is always fetched server-side. | Defer. |
| **Keyboard shortcuts** | Nice to have (Cmd+K for new conversation, etc.). | Defer. |
| **Conversation rename** | The sidebar shows the preview text. Explicit naming is not in the PRD. | Defer. |
| **Accessibility audit** | `aria-label`, focus management, screen reader support. The tool is for sighted users in a controlled internal environment. | Defer until required by policy. |
| **Browser compatibility below Chrome/Firefox/Safari latest-2** | Internal tool; browser is controlled. | Defer. |
| **Streaming abort / cancel button** | Stop a response mid-stream. Useful but not critical. | Defer to Phase 2. |

---

## 9. Dependencies and Risks

| Item | Risk | Mitigation |
|---|---|---|
| Backend API not yet deployed | Frontend cannot be tested end-to-end until the backend Tasks 11–12 (agent loop + SSE endpoint) are complete. | Use a local mock SSE server (a simple Python script that emits fake events) to unblock frontend development of Tasks F6–F9. |
| `marked.js` CDN availability | CDN outage breaks markdown rendering. | Acceptable for a dev-environment internal tool. For production, vendor the `marked.js` file into `src/static/js/vendor/`. Add this to a hardening task. |
| Payment preview text parsing | Claude's output format may vary slightly between model versions. The `detectPaymentPreview` heuristic may produce false negatives. | Log `console.warn` on failure. The fallback (user types confirmation) is safe. If false-negative rate is unacceptable in testing, request a structured `payment_preview` field in the backend's `done` event. |
| API key exposure in Jinja2 template | Passing `API_KEY` into the HTML page means it is visible in browser DevTools. | This is acceptable for an internal single-tenant tool where the user is the same person who configured the server. The key is not in any static file or source control. Document this constraint explicitly. |
| `fetch` streaming on older Safari | Safari <15.4 does not support `ReadableStream` with `fetch`. | Minimum browser requirement: Safari 15.4+ (released September 2021). Internal tool; this is enforceable. |
| Scroll behaviour during streaming | Forced scroll-to-bottom during streaming is annoying if the user has scrolled up to re-read something. | The 100px threshold check in Task F8 prevents forced scroll when the user has intentionally scrolled up. |

---

## 10. Complexity Estimates

| Task | Complexity | Sprint |
|---|---|---|
| F1 — Directory structure + CSS extraction | S | 1 |
| F2 — Two-column layout | S | 1 |
| F3 — `api.js` client module | S | 1 |
| F4 — `state.js` module | S | 1 |
| F5 — `renderer.js` module | M | 1 |
| F6 — `sse.js` module | M | 1 |
| F7 — Conversation list (load, select, create, delete) | M | 2 |
| F8 — Send message + stream response | M | 2 |
| F9 — Payment confirmation card | M | 2 |
| F10 — Error handling | S | 2 |
| F11 — Polish and loading states | S | 2 |

**Total: 5S + 5M across 2 sprints.** The work is predominantly medium complexity. No single task is L because there are no unknown algorithms — every piece is a direct translation of the API contract into DOM manipulation. The hardest engineering risk is in F6 (`sse.js` streaming) and F9 (payment card text parsing), both of which are explicitly bounded and have safe fallbacks.

---

## 11. Open Questions

1. **API key injection mechanism:** The plan assumes Jinja2 renders `{{ api_key }}` into the page. Confirm that `API_KEY` is accessible to the FastAPI template renderer (via `os.getenv`) and that this is acceptable to the security posture of the deployment environment.

2. **FastAPI static file serving:** `StaticFiles` from `starlette` requires `python-multipart` to be installed (it is a `starlette` extra, not included by default). Confirm this is added to `src/requirements.txt` alongside the FastAPI mount configuration in `api/main.py`.

3. **The existing `/chat` endpoint in `app.py`:** During the transition period, `src/templates/index.html` will be rewritten for the new API. This breaks the old `/chat` endpoint's UI for anyone using `app.py` directly. Confirm that `app.py` is not used in any shared environment during development of this frontend. If it is, a feature flag or a second template file is needed.

4. **`marked.js` version pinning:** The existing `index.html` loads `marked.js` from `cdn.jsdelivr.net/npm/marked/marked.min.js` with no version pin. The latest major version of `marked` introduced breaking changes. Pin to a specific version (e.g., `marked@9.1.6`) in the CDN URL to prevent silent breakage on major updates.
