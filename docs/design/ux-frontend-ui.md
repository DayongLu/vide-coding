# UX Specification: Frontend Web UI

**Product:** Finance Agent — AI-powered accounts payable assistant
**Status:** Draft
**Date:** 2026-04-13
**Author:** Design Manager
**Scope:** `src/templates/index.html` (full redesign from current single-view UI)
**API Reference:** `docs/design/ux-backend-api.md`
**Bill Payment Reference:** `docs/design/ux-bill-payments.md`

---

## Purpose of This Document

This specification governs every visual and interaction decision for the Finance Agent web UI. It is the gate document that must be committed before any frontend implementation work begins. Engineers implement against this document. If a detail is underspecified, raise it before writing a line of code.

---

## 1. Information Architecture

### 1.1 Mental Model

The product is a persistent chat interface for querying QuickBooks Online data and initiating bill payments. The mental model is: each conversation is a thread, and users accumulate threads over time the way they accumulate email threads or Slack conversations. This is not a single-use search box — it is a workspace.

### 1.2 Views

There are three distinct views. All views live on a single page (no full-page navigation).

| View ID | Name                  | Triggered by                                             |
|---------|-----------------------|----------------------------------------------------------|
| V-1     | No Conversations      | User has no prior conversations (first use)              |
| V-2     | Conversation List + Active Chat | One or more conversations exist; one is selected |
| V-3     | New Conversation      | User clicks "New conversation" before typing anything    |

There is no separate "settings" view in Phase 1. Authentication configuration is handled outside the UI (environment variables, as specified in the API contract Section 5).

### 1.3 Navigation Structure

The UI has two persistent regions: a sidebar and a main content area.

**Sidebar** (always visible on desktop)
- Header: product name and "New conversation" button
- Conversation list: scrollable, newest first, cursor-paginated
- Each item shows: preview text (truncated to ~50 chars), relative timestamp

**Main area** (context-dependent)
- V-1: Empty state panel (no conversation selected)
- V-2: Active conversation with message history and input
- V-3: New conversation empty state with suggested prompts

There is no top-level header bar in the redesigned layout. The current implementation has a full-width header; this is replaced by the sidebar header to reclaim vertical space for message content.

### 1.4 Page Title

`<title>` updates dynamically to reflect the active conversation:

- No conversation selected: `Finance Agent`
- Conversation selected: `[preview text] — Finance Agent`
- New conversation: `New Conversation — Finance Agent`

---

## 2. Layout and Wireframes

### 2.1 Desktop Layout (viewport >= 1024px)

```
+---------------------------+------------------------------------------------------+
|  SIDEBAR (260px fixed)    |  MAIN CONTENT AREA (flex: 1)                         |
|                           |                                                      |
|  Finance Agent      [+]   |  +-------------------------------------------------+ |
|  ─────────────────────    |  |  MESSAGE HISTORY (flex: 1, overflow-y: auto)    | |
|                           |  |                                                 | |
|  Apr 13                   |  |  [Tool badge: unpaid bills]                     | |
|  What bills are overdue?  |  |  You have 3 overdue bills totalling...          | |
|                           |  |  ──────────────────────────────────             | |
|  Apr 12                   |  |                         What bills are overdue? | |
|  Show me our vendors      |  |                                                 | |
|                           |  |  [Tool badge: vendors]                          | |
|  Apr 12                   |  |  Here are your current vendors:                 | |
|  Total accounts payable   |  |    | Vendor | Contact |                         | |
|                           |  |    | ...    | ...     |                         | |
|  ─────────────────────    |  |                                                 | |
|  Load more conversations  |  |  [PAYMENT PREVIEW CARD — if applicable]         | |
|                           |  |                                                 | |
|                           |  +-------------------------------------------------+ |
|                           |  |  TOOL STATUS BAR (visible during streaming)     | |
|                           |  |  Checking unpaid bills...                       | |
|                           |  +-------------------------------------------------+ |
|                           |  |  INPUT AREA                                     | |
|                           |  |  +-----------------------------------+ [Send]   | |
|                           |  |  | Ask about your QuickBooks data...  |          | |
|                           |  |  +-----------------------------------+          | |
|                           |  +-------------------------------------------------+ |
+---------------------------+------------------------------------------------------+
```

`[+]` is the "New conversation" icon button in the sidebar header.

### 2.2 Tablet Layout (768px – 1023px)

The sidebar collapses to a 48px icon rail showing only a list icon and the `[+]` button. Tapping the list icon slides the sidebar open as an overlay (300px wide, with a semi-transparent backdrop). The main content area occupies full width when the sidebar is collapsed.

```
+-------+------------------------------------------------------+
| [=][+]|  MAIN CONTENT AREA (full width)                      |
|       |  ...                                                  |
|       |  INPUT AREA                                           |
+-------+------------------------------------------------------+

On tap of [=]:
+---------------------------------------+
| SIDEBAR OVERLAY (300px)               |
|  Finance Agent              [x close] |
|  ─────────────────────────────────    |
|  Apr 13  What bills are overdue?      |
|  Apr 12  Show me our vendors          |
|  ...                                  |
+---------------------------------------+
(backdrop dims the main area behind the overlay)
```

### 2.3 Mobile Layout (< 768px)

Single-column layout. The sidebar does not exist as a persistent element. Navigation between conversations is accessed via a drawer triggered by a hamburger icon in a slim top bar (48px height).

```
+-------------------------------------------------------+
|  [=]  Finance Agent                             [+]   |  48px top bar
+-------------------------------------------------------+
|                                                       |
|  MESSAGE HISTORY                                      |
|  (flex: 1, overflow-y: auto)                          |
|                                                       |
|  [Tool badge]  Assistant message text here...         |
|                                                       |
|                           User message text here      |
|                                                       |
+-------------------------------------------------------+
|  TOOL STATUS BAR (conditional, 36px)                  |
+-------------------------------------------------------+
|  [Input field]                             [Send]     |  56px input area
+-------------------------------------------------------+
```

On mobile, the input row uses a full-width textarea (not single-line input) that auto-grows up to 3 lines before scrolling internally.

### 2.4 Sidebar Conversation Item Anatomy

```
+-----------------------------------------------+
|  Apr 13, 2:23 PM                              |  (relative if today; date if older)
|  What bills are overdue?                      |  (preview, truncated, no markdown)
+-----------------------------------------------+
```

Active item has a distinct background and left border accent. Hover state lightens the background slightly. The item is a single clickable target — there is no inline delete icon on hover (deletion is accessed via a context menu or a button inside the active conversation header).

### 2.5 Active Conversation Header

When a conversation is active, a slim bar (40px) sits above the message history, inside the main content area:

```
+------------------------------------------------------+
|  What bills are overdue?          [Delete this chat] |
+------------------------------------------------------+
```

"Delete this chat" is a text button, not an icon, to make its destructive intent scannable without relying on icon recognition. Clicking it triggers a confirmation dialog (see Section 5.3).

---

## 3. Chat Message Rendering

### 3.1 Message Bubble Anatomy

**User message:**
```
                         +-------------------------------+
                         |  What bills are overdue?      |
                         +-------------------------------+
                         2:23 PM
```

- Right-aligned
- Background: navy (`#1A1A2E`)
- Text: white
- Border radius: 12px, with bottom-right corner 4px (speech bubble shape)
- Max width: 70% of content area
- No markdown rendering — content is plain text, displayed verbatim
- Timestamp below, right-aligned, muted color

**Assistant message:**
```
+-----------------------------------------------------------------------+
| [badge: unpaid bills]                                                 |
|                                                                       |
|  You have **3 overdue bills** totalling $4,821.00:                    |
|                                                                       |
|  | Vendor    | Due Date | Amount    |                                 |
|  |-----------|----------|-----------|                                 |
|  | Acme Corp | Apr 1    | $2,100.00 |                                 |
|  | ...       | ...      | ...       |                                 |
|                                                                       |
+-----------------------------------------------------------------------+
2:23 PM
```

- Left-aligned
- Background: white
- Text: near-black (`#1A1A1A`)
- Border radius: 12px, with bottom-left corner 4px
- Box shadow: subtle (0 1px 4px rgba(0,0,0,0.08))
- Max width: 85% of content area (wider than user bubbles because assistant responses contain tables and structured data)
- Full markdown rendering (see Section 3.2)
- Tool badges above content (see Section 3.3)
- Timestamp below, left-aligned, muted

### 3.2 Markdown Rendering Rules

Assistant content is rendered from markdown using the `marked` library (already present in the current implementation). The following elements must be styled to match the finance context:

| Markdown element | Rendering specification |
|------------------|------------------------|
| `**bold**`       | `font-weight: 600`, color `#1A1A2E` (navy, not default black) |
| Tables           | Full width within bubble, collapsed borders, alternating row shading (white / `#F8F9FA`), header row `#F0F2F5` with `font-weight: 600` |
| `# Heading 1`   | `font-size: 15px`, `font-weight: 700`, margin top 16px, margin bottom 8px |
| `## Heading 2`  | `font-size: 14px`, `font-weight: 600`, margin top 12px |
| `### Heading 3` | `font-size: 13px`, `font-weight: 600`, margin top 8px |
| Unordered lists  | `padding-left: 20px`, `list-style: disc`, item gap 4px |
| Ordered lists    | `padding-left: 20px`, `list-style: decimal`, item gap 4px |
| Inline `code`    | Background `#F0F0F0`, padding 2px 5px, border-radius 3px, `font-size: 13px`, monospace font |
| Code blocks      | Background `#F6F8FA`, border 1px solid `#E1E4E8`, padding 12px 16px, border-radius 6px, monospace, `font-size: 13px`, horizontal scroll on overflow |
| `---` horizontal rule | 1px solid `#E0E0E0`, margin 12px 0 |
| Blockquotes      | Left border 3px solid `#D0D0D0`, padding-left 12px, color `#555` |

Currency values in assistant text are not specially parsed — the markdown renderer treats them as plain text. Claude's responses format them correctly.

### 3.3 Tool Call Badges

Tool badges appear at the top of assistant messages when `tools_called` is non-empty.

```
[chip: unpaid bills]  [chip: balance sheet]
```

Badge anatomy:
- Small pill chip: background `#EBF5FB`, border `1px solid #AED6F1`, text `#2471A3`
- Font size: 11px
- Padding: 3px 10px
- Border radius: 10px
- Icon: a small database icon (16px) preceding the text, color `#5DADE2`
- Label: tool name transformed by stripping `get_` prefix and replacing underscores with spaces (e.g., `get_unpaid_bills` → `unpaid bills`)
- Multiple badges wrap left-to-right with 4px gap
- Badges appear before the message content with 8px margin below

Historical conversations loaded from `GET /api/v1/conversations/{id}` must also show badges using the stored `tools_called` array on each message object.

### 3.4 Streaming State

During streaming (between sending a message and receiving the `done` SSE event), the UI goes through these phases:

**Phase 1 — Tool execution (zero or more tool_start/tool_end cycles)**

A tool status bar appears between the message history and the input area. It shows a spinner (animated, 16px) and the human-readable tool label:

```
+------------------------------------------------------+
|  (spinner)  Checking unpaid bills...                 |
+------------------------------------------------------+
```

The label is derived from the `tool_start` event's `tool` field using the same transformation as badges. When `tool_end` arrives, the label changes to the `summary` field for 800ms before disappearing or transitioning to the next tool's `tool_start`.

**Phase 2 — Text streaming (token events)**

An assistant message bubble appears immediately after the first `token` event. As tokens arrive, text appends to the bubble progressively. A blinking cursor (|, 1s blink period) appears at the insertion point to signal active streaming.

The markdown renderer is called on each token append, but partial markdown is tolerated gracefully — the renderer should not throw on incomplete tables or unclosed bold markers. The full re-render from `full_text` on the `done` event is the canonical final state.

**Phase 3 — Completion (done event)**

1. The blinking cursor is removed.
2. The bubble content is replaced with the final rendered markdown from `full_text`.
3. Tool badges are attached from `tools_called`.
4. The tool status bar is hidden.
5. The input field and Send button are re-enabled.
6. The conversation preview in the sidebar is updated to reflect the first user message.

**Input disabled state during streaming:**

The input field is disabled and visually dimmed (opacity 0.5) during streaming. The Send button shows "..." instead of "Send" and is disabled. This prevents double submission. Tab focus is removed from these elements during this state.

### 3.5 Typing Indicator (pre-first-token)

Between the user sending a message and the first SSE event arriving, show a typing indicator in place of the assistant bubble:

```
+----------------------------+
|  (dot)  (dot)  (dot)       |
+----------------------------+
```

Three dots with a staggered bounce animation (the current implementation's animation is correct). The indicator disappears as soon as the first `token` event or `tool_start` event is received.

---

## 4. Bill Payment Confirmation Flow

### 4.1 Flow Overview

The payment flow is initiated by the user in natural language (e.g., "Pay the Acme Corp bill"). Claude orchestrates the tool calls. The frontend's role is to surface the preview in a structured card and collect explicit confirmation or cancellation before Claude calls `create_bill_payment`.

The flow from the frontend perspective:

```
User sends payment intent message
  → Streaming: tool_start (get_bill_by_id or get_unpaid_bills)
  → Streaming: tool_end
  → Streaming: tool_start (preview_bill_payment)
  → Streaming: tool_end
  → Streaming: token events (Claude presents preview in text)
  → done event
  → UI detects "preview_bill_payment" in tools_called
  → UI renders Payment Preview Card below the assistant message
  → User clicks "Confirm Payment" or "Cancel"
  → User's choice is sent as the next chat message ("confirm" or "cancel")
  → Claude proceeds with create_bill_payment or acknowledges cancellation
```

### 4.2 Payment Preview Card

When an assistant message has `preview_bill_payment` in its `tools_called`, a Payment Preview Card is rendered immediately below the assistant message bubble (not inside it). The card is a distinct UI component, not part of the markdown text.

The data displayed in the card comes from Claude's markdown text (which paraphrases the preview result). The frontend does not receive the raw preview JSON directly — Claude summarizes it and the card is built from a structured parse of Claude's text, or alternatively, the backend may emit a dedicated SSE event for preview data in a future iteration. For Phase 1, the card is rendered from Claude's text output. See Section 4.3 for the card layout.

**Important:** The card's Confirm and Cancel buttons send messages through the normal chat flow. "Confirm Payment" sends the text "confirm" and "Cancel" sends "cancel". This keeps the confirmation in the conversation history and preserves the conversational audit trail.

### 4.3 Payment Preview Card Layout

```
+----------------------------------------------------------------------+
|  PAYMENT PREVIEW                                                     |
|  ─────────────────────────────────────────────────────────────────  |
|                                                                      |
|  Vendor           Acme Corp                                          |
|  Bill             #123                                               |
|  Payment amount   $1,500.00  (full balance)                          |
|  Payment date     April 12, 2026                                     |
|  Pay from         Business Checking                                  |
|  Account balance  $42,000.00  →  $40,500.00 after payment           |
|  Memo             —                                                  |
|                                                                      |
|  ─────────────────────────────────────────────────────────────────  |
|                                                                      |
|  [Cancel]                              [Confirm Payment]             |
|                                                                      |
+----------------------------------------------------------------------+
```

Card styling:
- Background: white
- Border: 1px solid `#F0B429` (amber, communicating financial action)
- Border-radius: 8px
- Left accent bar: 4px solid `#F0B429`
- Box shadow: 0 2px 8px rgba(0,0,0,0.12)
- Margin: 12px 0 0 0 (below the assistant message bubble)
- Max width: same as assistant message bubble (85% of content area)
- Internal padding: 20px 24px

Label-value rows:
- Two-column layout using CSS grid (160px label column, auto value column)
- Label: `font-size: 13px`, `font-weight: 500`, color `#555`
- Value: `font-size: 14px`, `font-weight: 600`, color `#1A1A1A`
- Currency amounts: `font-variant-numeric: tabular-nums`
- Arrow in balance row (`→`): color `#888`
- Divider line: 1px solid `#E0E0E0`

**Confirm Payment button:**
- Background: `#1A7F37` (green, signaling financial execution)
- Text: white
- Font weight: 600
- Padding: 10px 24px
- Border radius: 6px
- Right-aligned within the card footer
- On hover: `#155E2A`
- On focus: 2px offset outline, color `#1A7F37` (for keyboard navigation)

**Cancel button:**
- Background: transparent
- Border: 1px solid `#CCC`
- Text color: `#555`
- Font weight: 500
- Padding: 10px 16px
- Border radius: 6px
- Left-aligned within the card footer
- On hover: background `#F5F5F5`

### 4.4 Post-Confirmation State

After the user clicks "Confirm Payment":

1. Both buttons are immediately disabled and dimmed.
2. The Confirm button shows a spinner and the label changes to "Processing...".
3. The "confirm" message is sent through the normal chat message flow.
4. The card remains visible (do not remove it) while Claude processes.
5. When the `done` event arrives confirming payment, the card is replaced by a Payment Success Banner.

**Payment Success Banner:**

```
+----------------------------------------------------------------------+
|  (checkmark icon)  Payment recorded. Bill #123 fully paid.           |
|  $1,500.00 paid to Acme Corp on April 12, 2026.                      |
|  QBO Payment ID: qbo_pay_456                                         |
+----------------------------------------------------------------------+
```

- Background: `#EAFAF1`
- Border: 1px solid `#27AE60`
- Border-radius: 6px
- Icon: checkmark SVG, `#27AE60`
- Text: `font-size: 13px`, color `#1E8449`

After the user clicks "Cancel":

1. Both buttons are immediately disabled.
2. The "cancel" message is sent through the normal chat message flow.
3. The card collapses with a fade-out (200ms opacity transition).
4. Claude's acknowledgment of the cancellation appears as the next assistant message.

### 4.5 Token Expiry Warning

The confirmation token expires in 5 minutes (per `docs/design/ux-bill-payments.md`). If the Payment Preview Card has been visible for 4 minutes without user interaction, display an inline warning within the card:

```
  This preview expires in 1 minute. Confirm now or start over.
```

- Text color: `#E67E22` (amber warning)
- Font size: 12px
- Appears above the button row

At 5 minutes, if the card is still showing, the Confirm button is disabled and its label changes to "Preview expired". An inline message reads:

```
  This preview has expired. Send another message to start a new payment.
```

The input field is re-enabled at this point so the user can request a new preview.

---

## 5. Error States

Errors fall into two categories: recoverable (user can act to fix) and non-recoverable (user must start fresh or contact support). The API contract's `recoverable` boolean drives this distinction.

### 5.1 Inline Message Errors (Recoverable)

When a streaming `error` SSE event arrives with `recoverable: true`, or when a non-streaming HTTP error response has `recoverable: true`, display an error message inline in the conversation as an assistant-style bubble styled distinctively:

```
+-----------------------------------------------------------------------+
|  (warning icon)  Something went wrong                                 |
|  QuickBooks API is unavailable. Please try again shortly.             |
|                                                                       |
|  [Try again]                                                          |
+-----------------------------------------------------------------------+
```

- Background: `#FEF3C7` (light amber)
- Border-left: 3px solid `#F59E0B`
- Icon: triangle warning SVG, `#D97706`
- Title: `font-weight: 600`, `#92400E`
- Message: `font-size: 13px`, `#78350F`
- "Try again" button: text link style, color `#D97706`, underline on hover
- Clicking "Try again" re-sends the previous user message

Error messages to show for each error code (use the `message` field from the API response directly — it is written to be user-safe):

| Error code             | Title shown in UI                              |
|------------------------|------------------------------------------------|
| `UPSTREAM_AI_ERROR`    | Something went wrong                           |
| `UPSTREAM_QBO_ERROR`   | QuickBooks is unavailable                      |
| `CONVERSATION_NOT_FOUND` | This conversation could not be found         |
| `MESSAGE_TOO_LONG`     | Message too long                               |
| `EMPTY_MESSAGE`        | (prevented client-side; never reaches API)     |

### 5.2 Non-Recoverable Errors

When `recoverable: false`:

**Session-level error banner** (persists above the input area):

```
+----------------------------------------------------------------------+
|  (x icon)  A problem occurred that cannot be resolved in this chat.  |
|  Start a new conversation to continue.           [New conversation]  |
+----------------------------------------------------------------------+
```

- Background: `#FEE2E2`
- Border: 1px solid `#FCA5A5`
- Icon: X circle SVG, `#DC2626`
- Text: `#7F1D1D`
- The input field is disabled (with `aria-disabled="true"`)

For `UNAUTHORIZED` (401): redirect the entire page to a static "Authentication required" view that explains the API key must be configured. This is a full-view replacement, not a banner:

```
+-------------------------------------------------------+
|                                                       |
|  (lock icon, 48px)                                    |
|                                                       |
|  Authentication Required                              |
|  A valid API key is needed to use Finance Agent.      |
|                                                       |
|  Configure the API key in your environment settings   |
|  and refresh the page.                                |
|                                                       |
|  [Refresh page]                                       |
|                                                       |
+-------------------------------------------------------+
```

### 5.3 Confirmation Dialog — Delete Conversation

Deleting a conversation requires a confirmation dialog (not a browser `confirm()` call — a custom modal):

```
+---------------------------------------+
|  Delete this conversation?            |
|                                       |
|  "What bills are overdue?" and all    |
|  its messages will be permanently     |
|  removed. This cannot be undone.      |
|                                       |
|  [Cancel]          [Delete]           |
+---------------------------------------+
```

- Modal overlay: `rgba(0,0,0,0.4)` backdrop
- Modal box: white, border-radius 8px, padding 24px, max-width 400px, centered
- The preview text in the body is the conversation's preview string (first 50 chars)
- Delete button: background `#DC2626`, text white
- Cancel button: border `1px solid #CCC`
- Pressing Escape closes the dialog (equivalent to Cancel)
- Focus is trapped inside the modal while open (accessibility requirement)
- On confirm: delete request is sent, conversation is removed from sidebar, user is navigated to V-1 (empty state) or the next most recent conversation

### 5.4 Input Validation — Client-Side

The following validations happen before any API call:

| Condition                   | Behavior                                                           |
|-----------------------------|--------------------------------------------------------------------|
| Empty input on submit       | Shake animation on input field (200ms), no API call               |
| Input > 10,000 characters   | Counter shows "X / 10,000" in red below input; Send button disabled |
| Input > 9,500 characters    | Counter shows "X / 10,000" in amber as early warning               |

Character counter is hidden when input is below 9,500 characters (progressive disclosure — not visible noise at normal usage).

### 5.5 Network Connectivity Loss

If the SSE stream drops mid-response (browser `EventSource` fires an error event):

1. Show the tool status bar message: "(warning icon) Connection lost. Trying to reconnect..."
2. Wait 3 seconds, then attempt to reload the conversation from `GET /api/v1/conversations/{id}`.
3. If the reload succeeds and the message is present (it was persisted before the drop), display the complete message and clear the error.
4. If the reload succeeds but the message is not present (the request was lost), show a recoverable inline error with "Try again".
5. If the reload fails, show a non-recoverable banner.

---

## 6. Empty States

### 6.1 No Conversations (V-1): First Use

When `GET /api/v1/conversations` returns an empty `data` array:

```
+-----------------------------------------------------------------------+
|                                                                       |
|                      (chart icon, 48px, #1A1A2E)                     |
|                                                                       |
|               Ask me anything about your books                        |
|                                                                       |
|      I can look up vendors, bills, payments, accounts,                |
|      and reports from QuickBooks.                                     |
|                                                                       |
|      +--------------------+  +---------------------+                 |
|      | What bills are     |  | Show me our vendors  |                 |
|      | overdue?           |  |                      |                 |
|      +--------------------+  +---------------------+                 |
|                                                                       |
|      +--------------------+  +---------------------+                 |
|      | Total accounts     |  | Profit & Loss        |                 |
|      | payable?           |  |                      |                 |
|      +--------------------+  +---------------------+                 |
|                                                                       |
+-----------------------------------------------------------------------+
```

Suggested prompt chips:
- Background: white
- Border: 1px solid `#E0E0E0`
- Border-radius: 8px
- Padding: 12px 16px
- Font size: 13px, color `#1A1A2E`
- On hover: border-color `#1A1A2E`, background `#F8F9FA`
- Clicking a chip populates the input field and submits immediately

The sidebar shows the empty state:
```
+---------------------------+
|  Finance Agent      [+]   |
|  ─────────────────────    |
|                           |
|  No conversations yet.    |
|  Start one using the      |
|  input on the right.      |
|                           |
+---------------------------+
```

### 6.2 New Conversation (V-3): Sidebar Has Items but New is Selected

When the user clicks `[+]` (new conversation), a conversation is created via `POST /api/v1/conversations` immediately (to obtain a `conversation_id`). The main area shows the same suggested prompt chips as V-1, but the sidebar now shows the new item at the top with no preview text:

```
+---------------------------+
|  Finance Agent      [+]   |
|  ─────────────────────    |
|  >  New conversation      |  (active, no preview)
|  ─────────────────────    |
|  Apr 13                   |
|  What bills are overdue?  |
|  ...                      |
+---------------------------+
```

The "New conversation" item is removed from the sidebar list if the user navigates away without sending a message. The backend conversation shell (created optimistically) is cleaned up server-side on next load (the sidebar will not show conversations with empty `preview` strings).

### 6.3 Loading State for Conversation History

When the user clicks a sidebar item and the conversation is being fetched:

- The main area shows skeleton placeholders (not a spinner):

```
+-----------------------------------------------------------------------+
|  [skeleton: 60% width, 20px height, pulsing gray]                     |
|                                                    [skeleton: 40%]    |
|  [skeleton: 80% width, 60px height, pulsing gray]                     |
|  ...                                                                   |
+-----------------------------------------------------------------------+
```

Skeleton color: `#E0E0E0`, pulsing animation between `#E0E0E0` and `#F0F0F0` at 1.5s period.

---

## 7. Responsive Design

### 7.1 Breakpoints

| Breakpoint  | Range           | Layout                              |
|-------------|-----------------|-------------------------------------|
| Mobile      | 0 – 767px       | Single column, drawer navigation    |
| Tablet      | 768px – 1023px  | Icon rail sidebar, overlay drawer   |
| Desktop     | 1024px+         | Full fixed sidebar                  |

### 7.2 Mobile-Specific Considerations

**Keyboard and input:**
The mobile browser virtual keyboard reduces viewport height significantly when the user focuses the input. The layout must handle this:
- The message history area shrinks (flex layout handles this naturally if set up correctly)
- The input area stays pinned to the bottom (`position: sticky; bottom: 0`)
- The `<meta name="viewport">` must include `interactive-widget=resizes-content` to allow the browser to signal keyboard events
- Do not use `position: fixed` on the input bar — this breaks on iOS Safari

**Touch targets:**
All interactive elements must meet the minimum 44x44px touch target size. Specifically:
- Sidebar conversation items: minimum 48px height
- Send button: minimum 44x44px
- Confirm/Cancel buttons in payment card: minimum 44px height
- Suggestion chips: minimum 44px height
- "New conversation" button: 44x44px

**Message width:**
On mobile, both user and assistant messages expand to 95% of the content width (not 70%/85% as on desktop). Tables in assistant messages scroll horizontally within the bubble if wider than the screen.

**Payment Preview Card on mobile:**
The label-value grid changes from a two-column layout to a stacked single-column layout:
```
Vendor
  Acme Corp

Payment amount
  $1,500.00 (full balance)
```

Confirm and Cancel buttons stack vertically, full width, with Confirm on top.

### 7.3 Table Handling on Small Screens

Financial data tables from Claude's responses can exceed the mobile screen width. Tables inside assistant message bubbles:
- Wrap in a `div` with `overflow-x: auto`
- Minimum column width: 80px (prevents content from collapsing illegibly)
- The bubble itself does not scroll horizontally — only the table's container does

---

## 8. Color, Typography, and Component Style

### 8.1 Color Palette

| Role                        | Token name          | Value     | Usage                                          |
|-----------------------------|---------------------|-----------|------------------------------------------------|
| Brand navy                  | `--color-brand`     | `#1A1A2E` | User message background, headings, accents     |
| Brand navy dark             | `--color-brand-dark`| `#141422` | Header background, active sidebar item         |
| Brand navy hover            | `--color-brand-hover`| `#2A2A4E` | Button hover states                           |
| Surface white               | `--color-surface`   | `#FFFFFF` | Assistant message background, sidebar bg       |
| Surface grey                | `--color-surface-grey`| `#F0F2F5` | Page background                              |
| Surface grey light          | `--color-surface-light`| `#F8F9FA` | Table alternate rows, chip hover             |
| Text primary                | `--color-text`      | `#1A1A1A` | Body text                                      |
| Text muted                  | `--color-text-muted`| `#888888` | Timestamps, placeholders, secondary labels     |
| Text on brand               | `--color-text-on-brand`| `#FFFFFF` | Text on navy backgrounds                    |
| Border                      | `--color-border`    | `#E0E0E0` | Dividers, input borders                        |
| Tool badge background       | `--color-tool-bg`   | `#EBF5FB` | Tool chip fill                                 |
| Tool badge border           | `--color-tool-border`| `#AED6F1` | Tool chip stroke                              |
| Tool badge text             | `--color-tool-text` | `#2471A3` | Tool chip label                                |
| Success green               | `--color-success`   | `#27AE60` | Payment confirmation, success banners          |
| Success green bg            | `--color-success-bg`| `#EAFAF1` | Success banner background                      |
| Warning amber               | `--color-warning`   | `#F0B429` | Payment preview card border, expiry warning    |
| Warning amber bg            | `--color-warning-bg`| `#FEF3C7` | Recoverable error background                   |
| Danger red                  | `--color-danger`    | `#DC2626` | Delete button, non-recoverable error text      |
| Danger red bg               | `--color-danger-bg` | `#FEE2E2` | Non-recoverable error background               |
| Payment confirm green       | `--color-pay-confirm`| `#1A7F37` | Confirm Payment button                        |

All foreground/background color pairs must meet WCAG 2.2 AA contrast:
- White text on `#1A1A2E` navy: ratio 12.6:1 (passes AA and AAA)
- `#1A1A1A` on white: ratio 19.0:1 (passes)
- `#2471A3` on `#EBF5FB`: verify ratio > 4.5:1
- `#7F1D1D` on `#FEE2E2`: verify ratio > 4.5:1
- `#1E8449` on `#EAFAF1`: verify ratio > 4.5:1

### 8.2 Typography

**Font stack:**
```
-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif
```

This matches the current implementation and is appropriate for a finance tool — familiar, neutral, and high legibility.

| Role                     | Size  | Weight | Line height | Usage                              |
|--------------------------|-------|--------|-------------|------------------------------------|
| Product name (sidebar)   | 16px  | 700    | 1.2         | "Finance Agent"                    |
| Conversation preview     | 13px  | 400    | 1.4         | Sidebar list items                 |
| Conversation timestamp   | 11px  | 400    | 1.2         | Muted, sidebar and message         |
| Message body             | 14px  | 400    | 1.6         | Primary reading size for chat      |
| Message strong           | 14px  | 600    | 1.6         | Bold in assistant responses        |
| Table cell               | 13px  | 400    | 1.4         | Data tables in assistant messages  |
| Table header             | 13px  | 600    | 1.4         | Column labels                      |
| Badge label              | 11px  | 500    | 1.2         | Tool call chips                    |
| Input placeholder        | 14px  | 400    | 1.4         | Input field hint text              |
| Button label             | 14px  | 500    | 1.2         | Send, Confirm, Cancel              |
| Error title              | 14px  | 600    | 1.4         | Error state headlines              |
| Error body               | 13px  | 400    | 1.5         | Error description text             |

Do not use italics anywhere in the UI. Financial data must be presented upright for legibility.

### 8.3 Spacing and Grid

Base unit: 8px. All spacing values are multiples of 4px.

| Element                  | Spacing                                    |
|--------------------------|--------------------------------------------|
| Sidebar width            | 260px (desktop), 48px (tablet rail)        |
| Sidebar item padding     | 12px 16px                                  |
| Sidebar item gap         | 2px between items                          |
| Message bubble padding   | 12px 16px                                  |
| Message gap              | 16px between messages                      |
| Content area padding     | 24px (desktop), 16px (mobile)              |
| Input area padding       | 16px 24px (desktop), 12px 16px (mobile)   |
| Payment card padding     | 20px 24px                                  |
| Card row gap             | 10px                                       |
| Button min height        | 44px (accessibility minimum)              |

### 8.4 Component Inventory

The following is the complete set of reusable components. Engineering should implement each as a discrete, self-contained unit.

| Component                | Description                                                   |
|--------------------------|---------------------------------------------------------------|
| `ConversationListItem`   | Sidebar item with preview, timestamp, active state            |
| `UserMessage`            | Right-aligned bubble, plain text, timestamp                   |
| `AssistantMessage`       | Left-aligned bubble, markdown renderer, tool badges, timestamp |
| `ToolBadge`              | Pill chip with icon and label                                 |
| `ToolStatusBar`          | Spinner + label strip, conditional visibility                 |
| `TypingIndicator`        | Three-dot bounce animation                                    |
| `MessageInput`           | Text input, character counter, send button                    |
| `PaymentPreviewCard`     | Structured card with label-value grid and action buttons      |
| `PaymentSuccessBanner`   | Green confirmation strip                                      |
| `RecoverableError`       | Amber inline error with retry                                 |
| `NonRecoverableError`    | Red session banner                                            |
| `ConfirmationDialog`     | Modal with cancel and destructive confirm actions             |
| `SkeletonMessage`        | Pulsing placeholder for loading states                        |
| `SuggestionChip`         | Prompt suggestion button for empty states                     |
| `EmptyState`             | Icon + title + description + chip grid                        |

---

## 9. Accessibility

### 9.1 WCAG 2.2 AA Compliance Requirements

All requirements below target WCAG 2.2 Level AA.

### 9.2 Keyboard Navigation

Every interactive element must be reachable and operable by keyboard alone.

| Element                            | Keyboard behavior                                                |
|------------------------------------|------------------------------------------------------------------|
| Sidebar conversation items         | Tab to focus, Enter or Space to select                           |
| New conversation button            | Tab to focus, Enter or Space to activate                         |
| Message input                      | Tab to focus; Enter sends the message                            |
| Send button                        | Tab to focus, Enter or Space to send                             |
| Suggestion chips                   | Tab to focus, Enter to populate and send                         |
| Payment Confirm button             | Tab to focus, Enter or Space to confirm                          |
| Payment Cancel button              | Tab to focus, Enter or Space to cancel; Escape also cancels      |
| Delete button in conversation header | Tab to focus, Enter to open confirmation dialog               |
| Confirmation dialog                | Focus trapped inside; Escape closes (Cancel action)              |
| Sidebar overlay on tablet/mobile   | Escape closes the overlay                                        |
| "Try again" error link             | Tab to focus, Enter to retry                                     |

Tab order must follow visual reading order (left-to-right, top-to-bottom). The modal confirmation dialog must trap focus — Tab and Shift+Tab must cycle only between the modal's focusable elements.

### 9.3 Semantic HTML Requirements

| Element                       | Semantic element                                              |
|-------------------------------|---------------------------------------------------------------|
| Sidebar navigation            | `<nav aria-label="Conversations">`                            |
| Conversation list             | `<ul>` with `<li>` per item                                   |
| Active conversation item      | `aria-current="page"` on the active `<li>` or `<a>`          |
| Message history               | `<main role="main">`, messages in `<article>` per turn        |
| Each message (user)           | `<article aria-label="Your message">`                         |
| Each message (assistant)      | `<article aria-label="Assistant response">`                   |
| Tool status bar               | `<div role="status" aria-live="polite">`                      |
| Streaming text                | `aria-live="polite"` on the streaming bubble container        |
| Error messages                | `role="alert"` (assertive) for non-recoverable, `role="status"` for inline |
| Confirmation dialog           | `role="dialog"`, `aria-modal="true"`, `aria-labelledby` pointing to title |
| Loading skeleton              | `aria-hidden="true"` (decorative); `aria-busy="true"` on the container |
| Payment preview card          | `<section aria-label="Payment preview">`                      |
| Send button (disabled)        | `aria-disabled="true"`, `disabled` attribute                  |
| Input character counter       | `aria-live="polite"` for the counter region                   |

### 9.4 Screen Reader Behavior

**Message streaming:** The streaming bubble has `aria-live="polite"`. Screen readers will announce text as it streams. This is intentional — users relying on screen readers need to follow the response as it arrives. Do not use `aria-live="assertive"` for streaming (it would interrupt other announcements).

**Tool status bar:** `aria-live="polite"` announces tool execution status. The transition from "Checking unpaid bills..." to the next tool or to silence is announced automatically.

**Payment preview card:** The card should receive focus automatically when it appears, announced as "Payment preview, region". The Confirm button is the first focusable element after the card heading.

**Error alerts:** Non-recoverable errors use `role="alert"` which is implicitly `aria-live="assertive"`. Recoverable inline errors use `role="status"` with `aria-live="polite"` to avoid interrupting ongoing speech.

### 9.5 Color Independence

The UI must never use color alone to convey meaning:

| State                      | Color signal             | Additional signal                                   |
|----------------------------|--------------------------|-----------------------------------------------------|
| Active sidebar item        | Darker background        | Left border accent (4px), `aria-current="page"`     |
| Error state                | Red/amber background     | Warning or error icon + descriptive text            |
| Success state              | Green background         | Checkmark icon + descriptive text                   |
| Payment preview card       | Amber border             | "PAYMENT PREVIEW" text label + distinct card shape  |
| Disabled input             | Dimmed (opacity 0.5)     | `disabled` attribute + "..." label on button        |
| Tool badge                 | Blue chip                | Text label describing the tool                      |

### 9.6 Motion and Animation

The following animations are used in the UI:

| Animation              | Duration | Purpose                          |
|------------------------|----------|----------------------------------|
| Typing indicator dots  | 1.4s     | System status feedback           |
| Skeleton pulse         | 1.5s     | Loading state feedback           |
| Cursor blink           | 1.0s     | Streaming active indicator       |
| Input shake on empty   | 200ms    | Error prevention feedback        |
| Card fade-out          | 200ms    | Payment cancel transition        |

All animations must respect the user's `prefers-reduced-motion` media query. When reduced motion is preferred:
- Typing indicator: static dots, no bounce
- Skeleton pulse: static `#E0E0E0`, no pulse
- Cursor blink: static cursor, no blink
- Input shake: no animation; show inline error text instead
- Card fade-out: instant removal with no transition

```css
@media (prefers-reduced-motion: reduce) {
  /* All animation and transition properties set to none or 0ms */
}
```

### 9.7 Form and Input Accessibility

The message input field:
- `<label>` element must be present but can be visually hidden (`sr-only` class) with the visible placeholder serving as the hint. The label text: "Message to Finance Agent".
- `aria-label="Message to Finance Agent"` as a fallback if a visible label is not used
- `aria-required="true"`
- Error state (e.g., message too long): `aria-invalid="true"`, `aria-describedby` pointing to the character counter element
- `autocomplete="off"` to prevent browser autocomplete on the financial query field

The Send button:
- `aria-label="Send message"` (not just "Send") for screen reader clarity
- `aria-busy="true"` during streaming, reverts to `aria-busy="false"` on completion

---

## 10. Interaction Patterns Summary

| Interaction                         | Pattern                                                        |
|-------------------------------------|----------------------------------------------------------------|
| Send a message                      | Enter key or Send button; optimistic user bubble appears immediately |
| Start a new conversation            | Click `[+]`, conversation shell created, empty state shown     |
| Select a conversation               | Click sidebar item, history loaded with skeleton placeholders  |
| Delete a conversation               | Click "Delete this chat", confirmation modal, then removal     |
| Suggest prompt                      | Click chip, populates and submits in one action                |
| Confirm payment                     | Click "Confirm Payment" on card, sends "confirm" as message    |
| Cancel payment                      | Click "Cancel" on card or press Escape, sends "cancel"         |
| Retry failed request                | Click "Try again" in recoverable error bubble                  |
| Load more conversations             | Click "Load more conversations" at bottom of sidebar list      |
| Close sidebar on tablet/mobile      | Tap backdrop or press Escape                                   |
| Scroll message history              | Natural scroll; new messages auto-scroll to bottom unless user has scrolled up |

**Auto-scroll behavior:** The message history automatically scrolls to the bottom when a new user message is sent or when the first token of an assistant response arrives. If the user has manually scrolled up to read history, auto-scroll is suspended. A "Scroll to bottom" floating button appears at the bottom-right of the message area when the user is scrolled up and new content arrives:

```
          +-------------------+
          |  v  New messages  |
          +-------------------+
```

Button: background `#1A1A2E`, text white, border-radius 20px, shadow, 12px font size.

---

## 11. Design Decisions and Rationale

### 11.1 Sidebar Replaces Full-Width Header

The current implementation uses a full-width top header bar, leaving conversation history inaccessible without a "New Chat" reset. The sidebar pattern (familiar from Slack, Claude.ai, ChatGPT) provides persistent conversation history without sacrificing vertical space for chat content. This directly applies Nielsen Heuristic #3 (user control and freedom) by letting users return to any previous conversation.

### 11.2 Payment Card as a Distinct Component

The payment preview is rendered as a card below the assistant message, not as part of the markdown text. This applies Gestalt's law of figure/ground: the card stands apart visually as a distinct interactive object requiring action, versus the prose explanation inside the message bubble. The amber border communicates financial consequence without using alarming red (which is reserved for errors).

### 11.3 Confirm and Cancel Send as Messages

Rather than having the UI call the API directly with a confirmation flag, Confirm and Cancel translate to chat messages ("confirm" / "cancel"). This design decision keeps the full user intent in the conversation history (an audit trail), ensures Claude's response to the action is also in the thread (with tool calls logged), and avoids a separate UI state machine that would diverge from the conversational model. It is a deliberate simplification with a clear tradeoff: the conversation history is slightly noisy with one-word messages, but correctness and auditability outweigh that concern in a financial application.

### 11.4 Progressive Markdown Rendering During Streaming

Tokens are rendered progressively as they arrive rather than buffering to completion. This applies Nielsen Heuristic #1 (visibility of system status) — the user sees the response forming and knows the system is working. The risk of partial markdown rendering artifacts is managed by replacing the streamed content with `full_text` on the `done` event, eliminating any final artifact.

### 11.5 Skeleton Loading Over Spinners

Skeleton screens for conversation history loading (rather than a centered spinner) set better user expectations by showing the structure of what is coming. Per the Loading States principle, skeleton screens reduce perceived wait time by engaging the eye with content shape rather than an abstract loading indicator.

### 11.6 Suggested Prompts as Recognition Over Recall

The suggestion chips on the empty state directly implement Nielsen Heuristic #6 (recognition over recall). Users of a finance assistant tool may not know what to ask first. Showing representative prompts removes the blank-page problem and immediately communicates the product's capabilities without requiring the user to read documentation.

---

*This document is the authoritative UX specification for the Finance Agent frontend. All implementation decisions that deviate from this spec require explicit sign-off from the Design Manager before being committed.*
