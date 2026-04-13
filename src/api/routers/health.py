"""
Health and readiness endpoints for the Finance Agent API.

Neither endpoint requires authentication — load balancers and probes
do not carry API keys.
"""

import logging
import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import api.db as db_module

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> JSONResponse:
    """Liveness probe — always returns 200 if the process is running.

    Returns:
        JSON ``{"status": "ok"}``.
    """
    return JSONResponse({"status": "ok"})


@router.get("/ready")
async def ready() -> JSONResponse:
    """Readiness probe — checks database and QBO token file.

    Returns:
        HTTP 200 ``{"status": "ok"}`` when all checks pass, or
        HTTP 503 ``{"status": "degraded", "failed": [...]}`` otherwise.
    """
    failed: list[str] = []

    # 1. Database reachable
    try:
        conn = sqlite3.connect(str(db_module._db_path))
        conn.execute("SELECT 1")
        conn.close()
    except Exception:
        logger.exception("Readiness: database check failed")
        failed.append("database")

    # 2. QBO tokens file exists and is readable
    tokens_path = Path(os.path.dirname(__file__)).parent.parent / "tokens.json"
    if not tokens_path.exists():
        failed.append("qbo_tokens")

    if failed:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "failed": failed},
        )
    return JSONResponse({"status": "ok"})
