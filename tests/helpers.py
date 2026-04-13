"""
Shared assertion helpers for bill payment tests.
"""

import json


def assert_error_envelope(response_str, expected_error_code, expected_recoverable=None):
    """Assert that a JSON response string is a well-formed error envelope.

    Checks that:
    - ``status`` equals ``"error"``
    - ``error_code`` equals ``expected_error_code``
    - ``message`` is present
    - ``recoverable`` is present and is a boolean
    - ``recoverable`` equals ``expected_recoverable`` when provided

    Args:
        response_str: Raw JSON string returned by an MCP tool.
        expected_error_code: The exact string expected in ``error_code``.
        expected_recoverable: Optional bool. When provided, asserts the value
            of ``recoverable`` matches exactly.

    Returns:
        The parsed response dict, so callers can make additional assertions.
    """
    data = json.loads(response_str)
    assert data["status"] == "error", (
        f"Expected status='error', got '{data.get('status')}'"
    )
    assert data["error_code"] == expected_error_code, (
        f"Expected error_code='{expected_error_code}', got '{data.get('error_code')}'"
    )
    assert "message" in data, "Error envelope missing 'message' field"
    assert "recoverable" in data, "Error envelope missing 'recoverable' field"
    assert isinstance(data["recoverable"], bool), (
        f"'recoverable' should be bool, got {type(data['recoverable'])}"
    )
    if expected_recoverable is not None:
        assert data["recoverable"] == expected_recoverable, (
            f"Expected recoverable={expected_recoverable}, got {data['recoverable']}"
        )
    return data
