"""
Unit tests for src/payment_tokens.py.

These are pure Python tests — no HTTP mocking needed. The only external
dependency mocked is ``time.time`` for TTL-related tests.
"""

import re
import sys
import os
from unittest.mock import patch

import pytest

# Ensure src/ is on path (conftest.py also does this, but be explicit)
SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import payment_tokens
from payment_tokens import (
    TOKEN_TTL,
    TokenAlreadyUsedError,
    TokenExpiredError,
    TokenNotFoundError,
    clear_store,
    consume_token,
    generate_token,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOKEN_PATTERN = re.compile(r"^prev_[0-9a-f]{12}_[0-9]+$")

SAMPLE_PAYLOAD = {
    "bill_id": "123",
    "vendor_id": "55",
    "vendor_name": "Acme Corp",
    "amount": 1500.00,
    "payment_account_id": "456",
    "payment_account_name": "Business Checking",
    "payment_date": "2026-04-12",
    "memo": "",
}

# ---------------------------------------------------------------------------
# generate_token tests
# ---------------------------------------------------------------------------


def test_generate_token_happy_path_returns_formatted_string():
    """UT-TOK-01: Token matches the expected format ``prev_[0-9a-f]{12}_[0-9]+``."""
    token = generate_token(SAMPLE_PAYLOAD)
    assert _TOKEN_PATTERN.match(token), (
        f"Token '{token}' does not match expected pattern prev_<hex12>_<timestamp>"
    )


def test_generate_token_unique_per_call():
    """UT-TOK-02: Two calls with the same payload return distinct tokens."""
    token_a = generate_token(SAMPLE_PAYLOAD)
    token_b = generate_token(SAMPLE_PAYLOAD)
    assert token_a != token_b, "Two calls to generate_token must return different strings"


def test_generate_token_stores_correct_expiry():
    """UT-TOK-09: The stored entry's ``expires_at`` equals the generation time + TOKEN_TTL."""
    fixed_time = 1744684800.0  # 2026-04-12T14:00:00 UTC
    with patch("payment_tokens.time") as mock_time:
        mock_time.time.return_value = fixed_time
        token = generate_token(SAMPLE_PAYLOAD)

    entry = payment_tokens._store[token]
    assert entry["expires_at"] == fixed_time + TOKEN_TTL, (
        f"Expected expires_at={fixed_time + TOKEN_TTL}, got {entry['expires_at']}"
    )


def test_generate_token_stores_payload():
    """generate_token stores the original payload under the token key."""
    payload = {"bill_id": "999", "amount": 50.0}
    token = generate_token(payload)
    stored = payment_tokens._store[token]
    assert stored["payload"] == payload


# ---------------------------------------------------------------------------
# consume_token — happy path
# ---------------------------------------------------------------------------


def test_consume_token_first_use_returns_payload():
    """UT-TOK-03: First consume call returns the original payload dict."""
    token = generate_token(SAMPLE_PAYLOAD)
    result = consume_token(token)
    assert result == SAMPLE_PAYLOAD


def test_consume_token_marks_token_as_used():
    """After a successful consume, the store entry's ``used`` flag is True."""
    token = generate_token(SAMPLE_PAYLOAD)
    consume_token(token)
    assert payment_tokens._store[token]["used"] is True


# ---------------------------------------------------------------------------
# consume_token — error paths
# ---------------------------------------------------------------------------


def test_consume_token_second_use_raises_token_already_used_error():
    """UT-TOK-04: Second consume call raises TokenAlreadyUsedError."""
    token = generate_token(SAMPLE_PAYLOAD)
    consume_token(token)  # first use — should succeed
    with pytest.raises(TokenAlreadyUsedError):
        consume_token(token)  # second use — must raise


def test_consume_token_after_ttl_raises_token_expired_error():
    """UT-TOK-05: Token consumed after TTL raises TokenExpiredError."""
    fixed_time = 1744684800.0
    with patch("payment_tokens.time") as mock_time:
        mock_time.time.return_value = fixed_time
        token = generate_token(SAMPLE_PAYLOAD)

    # Advance clock by 301 seconds (past TTL of 300)
    with patch("payment_tokens.time") as mock_time:
        mock_time.time.return_value = fixed_time + 301
        with pytest.raises(TokenExpiredError):
            consume_token(token)


def test_consume_token_at_ttl_boundary_raises_token_expired_error():
    """UT-TOK-08: Token consumed at exactly TTL (>=300s) is considered expired.

    Spec says: boundary is exclusive — a token exactly TOKEN_TTL seconds old
    should be expired (the check should use >=, not >).

    NOTE: If the implementation uses strict ``>`` instead of ``>=`` for the
    expiry check, this test will fail. That is intentional — the test documents
    the contract and will surface the off-by-one as a bug for the developer to
    fix in the implementation (change ``time.time() > expires_at`` to
    ``time.time() >= expires_at`` in payment_tokens.consume_token).
    """
    fixed_time = 1744684800.0
    with patch("payment_tokens.time") as mock_time:
        mock_time.time.return_value = fixed_time
        token = generate_token(SAMPLE_PAYLOAD)

    # Advance clock by exactly TOKEN_TTL seconds — boundary is expired (not inclusive)
    with patch("payment_tokens.time") as mock_time:
        mock_time.time.return_value = fixed_time + TOKEN_TTL
        with pytest.raises(TokenExpiredError):
            consume_token(token)


def test_consume_token_unknown_token_raises_token_not_found_error():
    """UT-TOK-06: Unknown token string raises TokenNotFoundError."""
    with pytest.raises(TokenNotFoundError):
        consume_token("prev_notareal_0000000000")


def test_consume_token_empty_string_raises_token_not_found_error():
    """UT-TOK-07: Empty string token raises TokenNotFoundError."""
    with pytest.raises(TokenNotFoundError):
        consume_token("")


# ---------------------------------------------------------------------------
# clear_store
# ---------------------------------------------------------------------------


def test_clear_store_removes_all_tokens():
    """UT-TOK-10: After clear_store(), all tokens are gone."""
    token_a = generate_token(SAMPLE_PAYLOAD)
    token_b = generate_token({"bill_id": "456", "amount": 99.0})

    clear_store()

    with pytest.raises(TokenNotFoundError):
        consume_token(token_a)

    with pytest.raises(TokenNotFoundError):
        consume_token(token_b)


def test_clear_store_leaves_store_empty():
    """clear_store empties the internal _store dict."""
    generate_token(SAMPLE_PAYLOAD)
    generate_token({"bill_id": "999"})
    clear_store()
    assert len(payment_tokens._store) == 0


# ---------------------------------------------------------------------------
# Token TTL constant
# ---------------------------------------------------------------------------


def test_token_ttl_constant_is_300():
    """TOKEN_TTL must be 300 seconds as specified."""
    assert TOKEN_TTL == 300


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


def test_concurrent_generate_and_consume_no_corruption():
    """generate_token + consume_token under concurrent threads must not corrupt state."""
    import threading

    payment_tokens.clear_store()
    errors: list[Exception] = []
    consumed: list[str] = []
    lock = threading.Lock()

    def worker():
        try:
            payload = {"bill_id": "42", "payment_amount": 100.0}
            token = payment_tokens.generate_token(payload)
            result = payment_tokens.consume_token(token)
            with lock:
                consumed.append(result["bill_id"])
        except Exception as exc:
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Errors during concurrent access: {errors}"
    assert len(consumed) == 20
    assert all(b == "42" for b in consumed)


def test_concurrent_consume_same_token_only_one_succeeds():
    """Only one thread should successfully consume a given token."""
    import threading

    payment_tokens.clear_store()
    payload = {"bill_id": "99", "payment_amount": 50.0}
    token = payment_tokens.generate_token(payload)

    successes: list[int] = []
    lock = threading.Lock()

    def try_consume():
        try:
            payment_tokens.consume_token(token)
            with lock:
                successes.append(1)
        except (payment_tokens.TokenAlreadyUsedError, payment_tokens.TokenExpiredError):
            pass

    threads = [threading.Thread(target=try_consume) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(successes) == 1, f"Expected 1 success, got {len(successes)}"
