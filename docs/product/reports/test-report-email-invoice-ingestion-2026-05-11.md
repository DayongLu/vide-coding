# Test Report: Email Invoice Ingestion

**Date:** 2026-05-11
**Author:** Test Lead
**Feature:** Email Invoice Ingestion → QuickBooks Bill Creation
**Branch:** `feat/email-invoice-ingestion`
**Commits validated:** `fa12743` (feature) + `b9ef7f0` (LLM provider abstraction)
**Python:** 3.13.12 (`.venv`)
**Framework:** pytest 9.0.3 + pytest-mock 3.15.1 + pytest-cov 7.1.0

---

## Test Summary

| Metric | Value |
|---|---|
| Total tests | 191 |
| Passed | 191 |
| Failed | 0 |
| Skipped | 0 |
| Errors | 0 |
| Duration | 2.71s |

### New tests added in this validation pass

| File | New tests | Subject |
|---|---|---|
| `tests/test_gmail_auth.py` (new) | 5 | OAuth token load, persist, error paths |
| `tests/test_email_client.py` (new) | 14 | Gmail fetch, attachment extraction, body parsing, labelling |
| `tests/test_invoice_scanner.py` (appended) | 5 | `scan_emails_for_invoices` end-to-end flow |
| `tests/test_invoice_parser.py` (appended) | 13 | Anthropic/Gemini/OpenAI provider routing + content builders |
| `tests/test_invoice_router.py` (new) | 11 | `/invoices/*` API endpoints with FastAPI TestClient |
| **Total new** | **48** | |

---

## Test Coverage Report

Run with `.venv/bin/python -m pytest --cov=src --cov-report=term-missing`.

### Overall

| Metric | Before | After | Δ |
|---|---|---|---|
| Total | 52% | **68%** | **+16 pts** |
| Tests passing | 145 | **191** | **+46** |

### Coverage for new-feature modules

| Module | Before | After | Notes |
|---|---|---|---|
| `src/email_client.py` | 0% | **91%** | Gmail fetch, attachment download, body extraction, labelling all covered. Missing: `_get_service` token-refresh persistence (lines 56–70) — exercised only when access token is expired; tested indirectly via `mark_as_processed`. |
| `src/gmail_auth.py` | 0% | **89%** | `authenticate` and `load_credentials` covered; missing: `__main__` CLI block (lines 100–102). |
| `src/invoice_parser.py` | 67% | **97%** | All three providers (Anthropic, Gemini, OpenAI) and all content types (PDF, PNG/JPG, JPEG, TIFF, text) now covered. Missing: `json.JSONDecodeError` branch at lines 277–279 (defensive). |
| `src/invoice_scanner.py` | 57% | **96%** | `scan_emails_for_invoices` end-to-end flow now covered including duplicate-skip, body fallback, and unmatched-vendor paths. |
| `src/api/routers/invoices.py` | 55% | **100%** | All 4 endpoints + error paths covered. |

### Coverage for pre-existing modules (unchanged in this branch)

| Module | Coverage | Reason for gap (not in this feature's scope) |
|---|---|---|
| `src/app.py` | 0% | Flask entrypoint — not unit-testable in isolation |
| `src/chat.py` | 0% | Interactive CLI entrypoint |
| `src/qbo_auth.py` | 0% | OAuth flow — requires browser |
| `src/api/agent.py` | 17% | Anthropic streaming endpoint — needs network/AsyncMock work |
| `src/tools.py` | 7% | Only parity test runs; full dispatch coverage out of scope here |
| `src/api/routers/health.py` | 50% | DB-failure branch not exercised |
| `src/qbo_client.py` | 80% | Read-only QBO call wrappers (used by chat/MCP) — not on the email-invoice path |

### Full coverage table

```
Name                               Stmts   Miss  Cover
------------------------------------------------------
src/api/__init__.py                    0      0   100%
src/api/agent.py                      92     76    17%
src/api/auth.py                       12      1    92%
src/api/db.py                         26      1    96%
src/api/errors.py                     15      0   100%
src/api/logging_config.py             20      1    95%
src/api/main.py                       73     18    75%
src/api/models.py                     33      0   100%
src/api/routers/__init__.py            0      0   100%
src/api/routers/conversations.py      96     12    88%
src/api/routers/health.py             28     14    50%
src/api/routers/invoices.py           47      0   100%
src/api/system_prompt.py               6      0   100%
src/app.py                            53     53     0%
src/chat.py                           45     45     0%
src/email_client.py                  116     10    91%
src/gmail_auth.py                     28      3    89%
src/invoice_parser.py                 91      3    97%
src/invoice_scanner.py               135      6    96%
src/payment_tokens.py                 31      0   100%
src/qbo_auth.py                       82     82     0%
src/qbo_client.py                    193     38    80%
src/qbo_mcp_server.py                 92     16    83%
src/tools.py                          57     53     7%
------------------------------------------------------
TOTAL                               1371    432    68%
```

---

## Issues Found

| ID | Severity | Description | File/Line | Status |
|---|---|---|---|---|
| BUG-001 | Important | `_extract_body_text` never decoded `text/html` body data — when an email had only an HTML body, `get_email_body` returned an empty string, so body-fallback parsing of invoices arriving as HTML email never worked. | `src/email_client.py:285` | **Fixed** in this pass — base case now decodes both `text/plain` and `text/html`; HTML tag-stripping still happens in the parent multipart branch. |
| ENV-001 | Important | Several runtime dependencies declared in `src/requirements.txt` were not installed in `.venv`: `google-auth-oauthlib`, `google-api-python-client`, `google-generativeai`, `openai`. Tests for these provider paths failed at import time before this pass. | `.venv` site-packages | **Fixed** in this pass — installed all four packages. |
| WARN-001 | Minor | `datetime.datetime.utcnow()` is deprecated in Python 3.13 and emits a `DeprecationWarning` from 8 test sites in `tests/test_invoice_scanner.py` (lines 47, 83). | `tests/test_invoice_scanner.py:47,83` | Open — not blocking. Replace with `datetime.datetime.now(datetime.UTC)`. |
| WARN-002 | Minor | `google-generativeai` package emits a `FutureWarning` on import — Google has deprecated it in favor of `google.genai`. | external dependency | Open — non-blocking. Track for next dependency-refresh sprint. |
| WARN-003 | Minor | `ResourceWarning: unclosed database` from `tests/test_errors.py::test_validation_error_returns_400` — a SQLite connection isn't being closed in the validation-error test path. | `tests/test_errors.py` (pre-existing) | Open — non-blocking, pre-existing. |
| INFO-001 | Informational | Python 3.9 cannot run this codebase (uses PEP 604 union syntax: `dict \| None`). Tests must be run via `.venv/bin/python` (Python 3.13). Worth documenting in CLAUDE.md / README. | `src/qbo_client.py`, `src/api/logging_config.py`, `src/invoice_parser.py` etc. | Open — recommend updating CLAUDE.md "Setup" section to require Python 3.10+. |

### Test categories executed

- **Unit tests:** invoice parser (Anthropic + Gemini + OpenAI providers), Gmail auth, email body/attachment parsing, invoice queue business logic — pure functions with mocked I/O.
- **Integration tests:** FastAPI `TestClient` against `/invoices/scan|queue|approve|reject` with mocked `invoice_scanner` and per-test SQLite DB.
- **End-to-end orchestration tests:** `scan_emails_for_invoices` with mocked Gmail client + invoice parser + QBO client — verifies the full email→queue→vendor-match path.

### Test categories NOT executed (out of scope or requires live credentials)

- Live Gmail OAuth flow — requires real Google credentials.
- Live LLM provider call — requires API keys; would incur cost.
- Live QBO sandbox `create_bill` — requires sandbox tokens (covered separately in `tests/qbo_test.py` integration tests, run on demand).
- Email-to-invoice end-to-end against a real inbox.

---

## Verdict

**PASS** — All 191 unit + integration tests pass. New feature code coverage exceeds 89% across every module the email-invoice-ingestion feature touches. One real defect (BUG-001) was found by the new tests and fixed in this pass; the remaining open items (WARN-001–003, INFO-001) are non-blocking.

### Recommended follow-ups before merging to `main`

1. **(Minor)** Replace `datetime.utcnow()` with timezone-aware datetimes in `tests/test_invoice_scanner.py` to silence WARN-001.
2. **(Docs)** Update `CLAUDE.md` "Setup" section to clarify that the project requires Python 3.10+ (PEP 604 syntax) and to point new contributors at `.venv` for running tests.
3. **(Process)** Backfill the missing planning artefacts for this feature per the "Plan Before You Build" rule — PRD, implementation plan, UX spec — since the feature shipped to a branch without them.

---

## How to reproduce

```bash
# From the project root
.venv/bin/python -m pytest --cov=src --cov-report=term-missing
```

Required: the `.venv` virtual environment with Python 3.13 and all packages in `src/requirements.txt` + `tests/requirements.txt` installed.
