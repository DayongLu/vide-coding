"""
API key authentication dependency for the Finance Agent API.

All protected endpoints declare ``Depends(verify_api_key)``. The key is
read from the ``API_KEY`` environment variable at each request so that
rotating the key only requires a restart, not a redeploy.
"""

import logging
import os

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

_security = HTTPBearer(auto_error=False)


def verify_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
) -> None:
    """Validate the Bearer token against the ``API_KEY`` environment variable.

    Args:
        credentials: Injected by FastAPI from the ``Authorization`` header.

    Raises:
        HTTPException: 401 if the header is absent or the token is incorrect.
    """
    expected = os.getenv("API_KEY", "")
    if not expected:
        logger.warning("API_KEY env var is not set; all requests will be rejected")

    if credentials is None or credentials.credentials != expected:
        raise HTTPException(
            status_code=401,
            detail={
                "status": "error",
                "error_code": "UNAUTHORIZED",
                "message": "Missing or invalid API key.",
                "recoverable": False,
            },
        )
