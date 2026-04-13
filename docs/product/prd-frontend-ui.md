# PRD: Frontend Web UI for Finance Agent

**Status:** Draft
**Date:** 2026-04-13
**Author:** Product
**Scope:** Replacement of `src/templates/index.html` and the Flask `app.py` rendering layer with a proper frontend web application that consumes the new FastAPI backend (`src/api.py`)
**PRD Reference (backend):** `docs/product/prd-backend-api.md`
**API Contract Reference:** `docs/design/ux-backend-api.md`

---

## 1. Problem Statement

The current UI (`src/templates/index.html`) is a single-file Flask template built as a proof-of-concept alongside the original Flask server. It demonstrates the concept but falls short on every dimension that matters for a usable accounts payable assistant. The specific problems:

**No response streaming.** The current UI issues a POST to `/chat`, awaits a complete JSON response, and renders it all at once. For queries that require multiple QBO tool calls, this can take 10–20 seconds with the UI showing only a bouncing-dots animation and no feedback about what is happening. Users have no signal of progress and no way to know if something went wrong mid-request.

**No conversation history.** Every browser refresh is a clean slate. The conversation state lives in server RAM keyed to a session ID generated at page load time (`session_` + timestamp). There is no way to return to a previous conversation, and a server restart silently discards every in-flight session.

**No conversation list.** The application exposes one conversation at a time. A user cannot see or navigate to any prior session. The only navigation action is "New Chat", which wipes the current session.

**The bill payment flow has no frontend support.** The bill payment write operations (`preview_bill_payment`, `create_bill_payment`) were shipped in the previous sprint but the UI was never updated. There is no preview step, no confirmation dialog, and no visual differentiation between a read query and a write operation. A user has no way to review the payment details before Claude executes it.

**No error recovery UX.** When a request fails, the UI renders "Error: " + whatever string the server returned, inline, as an assistant message. There is no distinction between a transient network failure (retry is appropriate) and a permanent error (retry is not). There is no retry affordance.

**No responsiveness.** The layout is a fixed-height viewport with no responsive breakpoints. On a tablet or narrow browser window, the input area and message bubbles overflow or compress in ways that make the app difficult to use.

**No accessibility.** The current template has no ARIA roles, no keyboard navigation beyond the Enter-to-send shortcut, and no focus management. Screen readers cannot meaningfully navigate the chat history.

The jobs to be done, restated:

> **When I am an AP clerk reviewing outstanding obligations, I want a fast, legible interface that shows me real-time progress as the AI queries QuickBooks — so that I do not sit idle waiting and do not miss context about what the AI is doing.**

> **When I am a finance manager authorizing a bill payment, I want to see a clear preview of the payment details — vendor, amount, account — before I confirm execution, so that I cannot accidentally pay the wrong bill or the wrong amount.**

> **When I am an AP clerk resuming work after a break, I want to navigate back to a previous conversation — so that I do not have to re-ask the same questions I already got answers to.**

---

## 2. Goals

1. Replace the current single-file template with a frontend that consumes the new FastAPI backend exclusively via the API contract defined in `docs/design/ux-backend-api.md`.
2. Render streaming responses incrementally using Server-Sent Events, eliminating the silent-wait experience.
3. Show real-time tool-call progress so users understand what the AI is doing during multi-step queries.
4. Provide a conversation list and navigation so users can return to prior sessions.
5. Implement a two-step bill payment confirmation flow (preview then confirm) with clear visual differentiation from read queries.
6. Handle all defined error conditions with appropriate UX: inline errors for transient failures, retry affordances where `recoverable: true`, and graceful dead-ends where `recoverable: false`.
7. Deliver a layout that is usable on desktops and tablets, and that meets WCAG 2.1 AA accessibility standards for the core interaction paths.

---

## 3. User Stories and Acceptance Criteria

### Story 1 — Stream a response in real time

**As an** AP clerk,
**I want** to see Claude's response appear word-by-word as it is generated,
**so that** I am not waiting in the dark for 15 seconds on complex queries.

**Acceptance Criteria:**

- The frontend opens an SSE connection to `POST /api/v1/conversations/{id}/messages` with `Accept: text/event-stream`.
- Text begins appearing in the assistant message bubble within 3 seconds of the user submitting a query (assuming normal QBO API latency).
- New text is appended to the message bubble as each `token` event arrives; the bubble grows incrementally.
- The send button and message input are disabled while a stream is in progress.
- After the `done` event is received, the accumulated streaming text is replaced with the `full_text` value from the `done` payload, rendered as markdown. This ensures correct final rendering even if any token events were dropped.
- The input is re-enabled and focused after `done`.
- The message scroll position follows the bottom of the growing message automatically during streaming.

---

### Story 2 — See what the AI is doing during tool calls

**As an** AP clerk,
**I want** to see a status indicator like "Checking unpaid bills..." while the AI is querying QuickBooks,
**so that** I know the system is working and understand what it is doing.

**Acceptance Criteria:**

- When a `tool_start` SSE event is received, a status strip appears below the user's message (above the streaming assistant bubble) displaying a human-readable label. The label is derived by stripping the `get_` prefix and replacing underscores with spaces (e.g., `get_unpaid_bills` → "Checking unpaid bills...").
- When the corresponding `tool_end` event is received, the status strip is removed or replaced with a collapsed tool badge on the assistant message bubble.
- If multiple tools are called sequentially, the status strip updates to the current tool name.
- The tool badge on the completed assistant message is present for all assistant messages that have a non-empty `tools_called` array, including messages loaded from conversation history (not just live-streamed messages).
- Tool badges are visually distinct from message body text (e.g., pill-shaped tag) and are non-interactive.

---

### Story 3 — Navigate to a previous conversation

**As an** AP clerk,
**I want** to see a list of my recent conversations in a sidebar,
**so that** I can return to a prior session without repeating my questions.

**Acceptance Criteria:**

- The layout includes a conversation list sidebar visible on desktop (>= 768px viewport width).
- On mobile/narrow viewports, the sidebar is accessible via a toggle (hamburger or panel open button).
- Each list item displays: the `preview` text (first user message, truncated to 60 characters with ellipsis if longer) and a relative timestamp ("2 hours ago", "Yesterday").
- Conversations are ordered by `updated_at` descending (most recently active first).
- Clicking a conversation item loads the full conversation history via `GET /api/v1/conversations/{id}` and renders all messages.
- The active conversation is visually highlighted in the list.
- A "New Conversation" button at the top of the sidebar calls `POST /api/v1/conversations` and opens a blank chat.
- The list loads the first page (default limit 20) on initial load. A "Load more" affordance or infinite scroll loads additional pages using cursor-based pagination.
- The sidebar reflects a new conversation immediately when "New Conversation" is clicked (optimistic UI — add to top of list without waiting for a re-fetch).

---

### Story 4 — Delete a conversation

**As an** AP clerk,
**I want** to remove a conversation I no longer need,
**so that** my conversation list does not accumulate clutter.

**Acceptance Criteria:**

- Each conversation item in the sidebar has a delete affordance (e.g., a trash icon that appears on hover/focus).
- Clicking delete shows a brief inline confirmation ("Delete this conversation?" with Confirm / Cancel) before calling `DELETE /api/v1/conversations/{id}`.
- On confirmation, the item is removed from the sidebar list.
- If the deleted conversation was the active one, the main chat area resets to the empty/welcome state.
- If the delete call returns 404 (conversation already gone), the item is silently removed from the sidebar with no error shown to the user — the end state is the same.
- If the delete call returns a 5xx error, the item is not removed and an inline error message is shown ("Could not delete. Try again.").

---

### Story 5 — Bill payment confirmation flow

**As a** finance manager,
**I want** to review the details of a proposed bill payment before it is executed,
**so that** I can verify the vendor, amount, and bank account are correct before money leaves the account.

**Acceptance Criteria:**

- When the assistant message stream contains a `tool_end` event for `preview_bill_payment`, the completed assistant message renders a distinct confirmation card below the AI's explanatory text.
- The confirmation card displays: vendor name, bill amount, due date, payment account (bank account name), and payment date.
- The confirmation card has two buttons: "Confirm Payment" and "Cancel".
- Clicking "Confirm Payment" sends a follow-up user message to the conversation: "Yes, confirm the payment." This message is sent via the normal message flow (not a special API call), allowing Claude to call `create_bill_payment` using the confirmation token it already holds from the `preview_bill_payment` response.
- Clicking "Cancel" sends the follow-up user message: "Cancel the payment." This lets Claude acknowledge the cancellation in its reply.
- After either button is clicked, both buttons are disabled and a spinner/loading state is shown while the follow-up message streams.
- The confirmation card is not shown for messages loaded from history (i.e., `tools_called` containing `preview_bill_payment` in a historical message has no action buttons — the action has already been resolved).
- The confirmation card is visually distinct from a normal assistant message: a border or background color that signals a required action (e.g., amber/yellow-tinted card with a payment icon).
- If the assistant message does NOT contain a `preview_bill_payment` tool call, no confirmation card is shown.

---

### Story 6 — Recoverable error handling

**As an** AP clerk,
**I want** clear error messages with a retry option when something goes wrong,
**so that** I know the difference between a temporary glitch and a real problem.

**Acceptance Criteria:**

- When an SSE `error` event is received with `recoverable: true`, the assistant message bubble shows the `message` text from the error payload and a "Try again" button. The input is re-enabled.
- Clicking "Try again" re-submits the last user message (the current conversation's last user turn).
- When an SSE `error` event is received with `recoverable: false`, the assistant message bubble shows the `message` text and no retry button. A separate note reads: "Start a new conversation to continue."
- When an HTTP 502 or 500 is returned before the stream opens (i.e., a pre-stream HTTP error), the behavior is the same as a `recoverable: true` SSE error.
- When an HTTP 401 is returned, the UI shows a non-dismissable banner: "Authentication required. Check your API key configuration." No retry is offered.
- When an HTTP 404 is returned for a conversation (e.g., user navigates to a stale bookmark), the sidebar and main area reset to the empty state with a note: "This conversation no longer exists."
- Network connection loss (fetch throws, EventSource closes unexpectedly) is treated as a recoverable error.
- Error states do not render raw JSON or stack traces. Only the human-readable `message` string from the API error body is shown.

---

### Story 7 — Welcome state and suggested prompts

**As a** first-time or infrequent user,
**I want** the empty-state UI to suggest relevant questions I can ask,
**so that** I do not have to guess what the assistant can do.

**Acceptance Criteria:**

- When a conversation has no messages (either a new conversation or the initial load with an empty conversation list), the main chat area shows a welcome state with a short description and a set of suggested prompt chips.
- The suggested prompts in the initial version are (carried over from the current template, with additions for bill payment):
  - "What bills are overdue?"
  - "Show me our vendors"
  - "What is our total accounts payable?"
  - "Show me the profit and loss report"
  - "Pay bill from Acme Corp"
- Clicking a suggested prompt populates the message input and submits the message immediately, as if the user had typed it.
- The welcome state is hidden once the conversation has at least one message.

---

### Story 8 — Keyboard accessibility and focus management

**As a** keyboard-only user,
**I want** to navigate the application without a mouse,
**so that** the product is accessible to users who rely on keyboard navigation.

**Acceptance Criteria:**

- All interactive elements (sidebar items, delete buttons, send button, confirmation card buttons, suggested prompt chips) are reachable via Tab and Shift+Tab.
- The conversation list and chat message area have logical focus order: sidebar navigation first, then chat input.
- The message input receives focus automatically after the page loads, after a message is sent, and after a streaming response completes.
- When a new assistant message is fully rendered, focus is not forcibly moved to the message — screen readers should receive the new content via an ARIA live region announcement, not a focus jump.
- The confirmation card ("Confirm Payment" / "Cancel") receives focus when it first renders, so keyboard users are immediately aware a required action is present.
- Delete confirmation dialogs trap focus within the dialog until dismissed.
- All icon-only buttons (e.g., delete trash icon) have accessible labels via `aria-label`.
- The application is navigable and fully functional with keyboard alone (no mouse required for any user story in this PRD).

---

## 4. Functional Requirements

### 4.1 Layout Structure

The application is a two-panel layout:

- **Left panel — Conversation Sidebar** (fixed width, ~260px on desktop): "New Conversation" button at top; scrollable list of conversation items below; each item is a button with preview text, relative timestamp, and a hover-revealed delete icon.
- **Right panel — Chat Area** (flex-fills remaining width): welcome state when no messages exist; message list when a conversation is active; tool-status strip above streaming message; message input bar pinned to bottom.

On viewports narrower than 768px, the sidebar collapses off-screen and a toggle button in the header opens/closes it as an overlay drawer.

### 4.2 Message Rendering

- User messages are rendered as plain text (no markdown). They are right-aligned.
- Assistant messages are rendered as markdown using a client-side markdown parser. Left-aligned.
- Tables, lists, bold, inline code, and headings are all expected in assistant output and must render correctly.
- During streaming, the assistant bubble renders the raw accumulated token text progressively. Partial markdown constructs (e.g., an incomplete table row) must not crash the renderer — either render gracefully or buffer until a complete markdown block is detected.
- After the `done` event, replace the streamed text with the `full_text` value, re-parsed through the markdown renderer. This is the canonical final render.
- Tool badges appear above the assistant message content. Each badge shows the human-readable tool label (same transformation as the status strip: strip `get_` prefix, replace underscores with spaces).

### 4.3 Streaming Implementation

- Use the `fetch` API with `ReadableStream` to consume SSE rather than the `EventSource` API. `EventSource` does not support custom headers, and the backend requires an `Authorization: Bearer` header on every request. `fetch` + streaming body parsing allows the auth header to be attached.
- Parse the SSE event format manually from the stream: split on double newline boundaries, extract `event:` and `data:` lines, parse `data:` as JSON.
- On stream open (first bytes received), create the assistant message bubble immediately so the user sees a response has begun.
- All `token` events append to the bubble content.
- `tool_start` events create or update the status strip.
- `tool_end` events clear the status strip.
- `done` event finalizes the message, attaches tool badges, stores `message_id`.
- `error` event renders the error state as described in Story 6.
- If the stream connection drops before a `done` or `error` event, treat as a recoverable error.

### 4.4 Conversation State Management

- On page load, call `GET /api/v1/conversations` (limit 20) to populate the sidebar.
- Store the active conversation ID in memory and in the URL (`?conversation=<uuid>`) so the page can be bookmarked and refreshed.
- On page load with a `?conversation=` URL parameter, call `GET /api/v1/conversations/{id}` to load the conversation. If 404, show the "conversation no longer exists" state and remove the query parameter.
- The in-memory conversation list is the source of truth for the sidebar. API responses update it — the sidebar does not re-fetch from the API on every action.
- When `POST /api/v1/conversations/{id}/messages` returns a `done` event, update the sidebar item's `updated_at` and `preview` (if this was the first user message in the conversation) optimistically.

### 4.5 Bill Payment Confirmation Card

- After a streaming message that includes `preview_bill_payment` in its `tools_called` array completes (i.e., `done` is received), check the `full_text` for a structured payment preview. The server instructs Claude to format the preview as a list of key-value pairs (vendor, amount, account, date). Parse those values from the rendered text to populate the confirmation card fields.
- Alternative: the backend may surface payment preview details via a structured `tool_end` summary for `preview_bill_payment`. If that summary follows a parseable format, use it. The exact mechanism is an integration detail to be finalized with the Tech Lead during implementation, but the UX requirement (a distinct card with readable payment fields before confirmation) is fixed.
- The confirmation card must only appear on the most recent assistant message in an active conversation, never on historical messages.
- The card must be keyboard accessible (Tab to reach, Enter or Space to activate buttons).

### 4.6 API Key Configuration

- The frontend reads the API key from a JavaScript configuration object that is injected by the server at render time (e.g., a `<script>` tag that sets `window.FINANCE_AGENT_CONFIG = { apiKey: '...' }`). This keeps the key out of static assets and allows the server to control it via environment variable.
- The key is attached as `Authorization: Bearer <key>` on every API call.
- The key is never written to `localStorage`, `sessionStorage`, or any persistent browser storage.
- The key is never logged to the browser console.

---

## 5. Non-Functional Requirements

### 5.1 Performance

| Requirement | Target |
|---|---|
| Time to first visible token | < 3.5 seconds from message submit to first character appearing (adds ~500ms network/parse margin over the backend's < 3s TTFT target) |
| Conversation list load time | < 1 second from page load to sidebar populated (GET /api/v1/conversations) |
| Historical conversation load time | < 1 second from click to messages rendered |
| Markdown render blocking | Markdown parsing must not block the main thread during streaming; use `requestAnimationFrame` batching or a Worker if token throughput causes jank |

### 5.2 Responsiveness

| Breakpoint | Behavior |
|---|---|
| >= 1024px (desktop) | Sidebar visible, fixed-width alongside chat area |
| 768px – 1023px (tablet) | Sidebar collapses; accessible via toggle button in header |
| < 768px (mobile) | Sidebar collapses; input area stacks vertically if needed; message bubbles expand to 95% width |

The layout must not introduce horizontal scroll on any supported viewport width.

### 5.3 Accessibility

- Target: WCAG 2.1 Level AA for all core user stories (Stories 1–7).
- Chat message list has `role="log"` and `aria-live="polite"` so screen readers announce new messages without interrupting ongoing announcements.
- The streaming assistant bubble has `aria-live="off"` during streaming (to avoid flooding the screen reader with every token) and changes to `aria-live="polite"` after `done` is received, triggering a single announcement of the completed message.
- Confirmation card has `role="alertdialog"` with an accessible description to ensure screen readers announce the required action.
- Color contrast ratios meet AA minimums (4.5:1 for normal text, 3:1 for large text and UI components).
- The application must not rely on color alone to convey meaning (e.g., the confirmation card's amber color must be accompanied by a text label or icon with an accessible name).

### 5.4 Browser Support

- Modern evergreen browsers: Chrome 120+, Firefox 120+, Safari 17+, Edge 120+.
- No IE11 support required.
- The `fetch` streaming API (`ReadableStream`) is available in all target browsers.

### 5.5 Bundle and Dependency Constraints

- No build system is required. The frontend must run directly from the files served by the backend with no compile step.
- Third-party dependencies must be loaded from a CDN (as the current template does with `marked.min.js`) or vendored as static files. No npm/bundler required.
- The total page weight (HTML + CSS + JS, excluding CDN assets) must not exceed 150 KB.

---

## 6. Tech Stack Recommendation

**Recommendation: Plain HTML + CSS + Vanilla JavaScript (no framework, no build step)**

Rationale:

The backend is a Python single-developer project. The frontend is a single page with a small, well-defined interaction surface: a sidebar, a chat area, a streaming message renderer, and one confirmation card pattern. This is not a candidate for React or Vue.

React would add: a build system (Webpack or Vite), JSX compilation, a component tree, a state management decision (useState vs. Redux vs. Zustand), and framework-specific SSE handling. None of these provide functional benefit for this scope. They do add maintenance surface and onboarding complexity for a single developer.

The existing template already proves the concept with vanilla JS and CDN-loaded `marked.min.js`. The new frontend extends this pattern rather than replacing the technology.

Specific choices:

| Concern | Choice | Rationale |
|---|---|---|
| Markdown rendering | `marked.js` (CDN) | Already in use; well-maintained; handles the markdown subsets Claude produces |
| SSE parsing | Hand-rolled using `fetch` + `ReadableStream` | Required for auth header support; EventSource does not support custom headers |
| Styling | Plain CSS with CSS custom properties for theming | No preprocessor needed; sufficient for the layout complexity |
| State management | Plain JS module with a single state object | No reactive framework needed; state updates are infrequent and predictable |
| DOM manipulation | Vanilla JS (`createElement`, `insertBefore`, `classList`) | No virtual DOM overhead for a page that renders < 100 elements at a time |
| URL state | `URLSearchParams` and `history.pushState` | Native API; no router library needed |

If the product grows to multiple distinct pages (e.g., a settings page, a payments history page, a reporting dashboard), the tech stack decision should be revisited. The threshold for introducing a framework is when the number of distinct pages exceeds 3 and shared state across pages becomes complex to manage in vanilla JS.

---

## 7. Out of Scope

The following are explicitly NOT part of this PRD:

- **Message editing or regeneration.** Users cannot edit a sent message or request a regenerated response.
- **Markdown source view.** Users cannot view the raw markdown behind an assistant response.
- **Export or sharing.** No "download conversation as PDF/CSV" or shareable link functionality.
- **Theming or dark mode.** A single visual theme is sufficient for Phase 1.
- **Notification or sound effects.** No browser notifications, no audio cues when a response completes.
- **Multi-user or per-user views.** Phase 1 is single-tenant (one API key, one user). The sidebar shows all conversations in the database.
- **File uploads or attachments.** Text-only input.
- **Message search.** No full-text search across conversation history.
- **Pagination within a conversation.** All messages in a conversation are loaded at once. Conversations with > 100 messages are a future concern.
- **Offline support or PWA.** No service worker, no offline cache.
- **Analytics or usage tracking.** No event tracking, no session recording.
- **The `chat.py` CLI interface.** This PRD does not affect the CLI.
- **The MCP server.** Unaffected.
- **Authentication UI (login page, API key entry form).** Phase 1 relies on a server-injected API key. No login flow is needed until multi-user auth is implemented.

---

## 8. RICE Score

| Factor | Estimate | Rationale |
|---|---|---|
| **Reach** | 10 / 10 | The web UI is the only user-facing interface for AP clerks and finance managers who are not developers. Every non-developer user is blocked without it. |
| **Impact** | 8 / 10 | Streaming removes the worst UX pain point. The bill payment confirmation flow is a safety-critical UX gap. Conversation history directly addresses user retention. |
| **Confidence** | 8 / 10 | The backend API contract is fully specified. The scope is well-understood. The tech stack is proven (vanilla JS + marked.js already in use). Main uncertainty is the exact parsing mechanism for the payment preview card. |
| **Effort** | 2 sprints | Sprint 1: layout, conversation list, streaming chat. Sprint 2: bill payment confirmation flow, error handling UX, accessibility pass. |

**RICE Score = (10 x 8 x 0.8) / 2 = 32**

This is the second-highest-scoring item in the backlog, behind the backend API (RICE 40.5) which is a hard dependency. Frontend work begins only after the backend API endpoints are verified and testable.

**Dependency:** `prd-backend-api.md` must be fully implemented and the API contract validated before Sprint 1 of this PRD begins. The frontend team cannot build against the new API until it exists.

---

## 9. Success Metrics

### Primary (user experience)

| Metric | Target | Measurement |
|---|---|---|
| Time to first visible token | < 3.5 seconds (P95) | Browser performance timeline; measured from send button click to first character visible in DOM |
| Zero unconfirmed bill payment executions | 0 instances | Any `create_bill_payment` tool call in production logs must be preceded by a `preview_bill_payment` in the same conversation turn and a user confirmation message |
| Conversation resume rate | >= 25% of sessions load a prior conversation from the sidebar within the first 30 days post-launch | Measured via server access logs: ratio of `GET /api/v1/conversations/{id}` (history loads) to `POST /api/v1/conversations` (new conversation) |

### Secondary (quality)

| Metric | Target | Measurement |
|---|---|---|
| WCAG 2.1 AA compliance | Zero AA violations on core user stories | axe-core automated scan + manual keyboard navigation test |
| Error recovery usage | >= 80% of `recoverable: true` error states result in a successful retry within 60 seconds | Inferred from server logs: error event followed by same-conversation message within 60 seconds |
| Mobile usability | Layout renders without horizontal scroll on 375px wide viewport (iPhone SE) | Manual test on target breakpoint |

### Guardrail (inherited from bill payments PRD)

| Metric | Alert Threshold | Action |
|---|---|---|
| Bill payment executed without preview | 0 tolerance | Any `create_bill_payment` in QBO audit log not preceded by user-visible confirmation card triggers an incident review |

---

## 10. Dependencies and Risks

| Item | Type | Notes |
|---|---|---|
| Backend API — `prd-backend-api.md` | Hard dependency | Frontend cannot be built against the new API until it exists and is locally runnable. Do not begin Sprint 1 until the API passes its integration tests. |
| `marked.js` CDN availability | Dependency | If the CDN is unavailable, markdown renders as raw text. Mitigation: vendor `marked.min.js` as a static file served by the backend. |
| `fetch` + `ReadableStream` SSE parsing | Dependency | All target browsers support this, but the hand-rolled SSE parser is not battle-tested. Mitigation: write a unit test for the SSE parser against representative event sequences from the API contract spec. |
| Bill payment preview card — data extraction | Risk | The confirmation card fields (vendor, amount, account, date) must be parsed from either Claude's `full_text` or the `tool_end` summary for `preview_bill_payment`. The exact format of these values is not yet pinned down. **Mitigation:** Before Sprint 2 begins, the Tech Lead must define and document the exact format of the `tool_end` summary for `preview_bill_payment`, or the backend must embed the payment preview fields in a structured SSE event. This is a cross-team dependency that must be resolved in Sprint 1 planning. |
| CSS layout complexity for sidebar | Risk | A hand-rolled sidebar with responsive collapse is non-trivial in plain CSS. Budget 1–2 days for the sidebar layout and mobile drawer behavior. If it blocks progress, a simplified mobile layout (no sidebar on mobile, access history via a dedicated screen) is an acceptable MVP trade-off. |
| Accessibility audit scope | Risk | A full WCAG 2.1 AA audit is a significant effort. Mitigation: run `axe-core` automated scan as part of the definition of done for Sprint 2. Focus manual testing on the five highest-risk flows: screen reader announcement of streaming messages, keyboard navigation to the confirmation card, focus trap in delete dialog, tab order in sidebar, and color contrast on tool badges. |
