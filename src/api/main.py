"""
FastAPI application factory for the Finance Agent API.

Usage:
    # Development
    python3.13 -m uvicorn api.main:app --reload --port 5001 --app-dir src

    # Production
    python3.13 src/api/main.py
"""

import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response

# Ensure src/ is on the path so invoice_scanner et al. can be imported
sys.path.insert(0, os.path.dirname(__file__) + "/..")

load_dotenv()

import api.db as db_module
from api.errors import register_exception_handlers
from api.logging_config import configure as configure_logging
from api.routers import conversations, health
from api.routers.invoices import router as invoices_router

logger = logging.getLogger(__name__)


def create_app(db_path: Path | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        db_path: Override the SQLite database path. Useful in tests
            (pass ``Path(':memory:')``) and for environment-based config.
            Defaults to ``DB_PATH`` env var or ``data/conversations.db``.

    Returns:
        Configured FastAPI instance.
    """
    configure_logging()

    resolved_db_path = db_path or Path(
        os.getenv("DB_PATH", "data/conversations.db")
    )
    db_module._set_db_path(resolved_db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db_module.init_db(resolved_db_path)
        logger.info("Database initialised at %s", resolved_db_path)

        # Start background email polling if Gmail is configured
        scheduler_task = None
        gmail_token_file = os.getenv("GMAIL_TOKEN_FILE", "src/gmail_tokens.json")
        poll_interval = int(os.getenv("EMAIL_POLL_INTERVAL_MINUTES", "15"))
        if Path(gmail_token_file).exists():
            logger.info(
                "Gmail configured — starting email poller (interval=%dm)", poll_interval
            )
            scheduler_task = asyncio.create_task(
                _email_poll_loop(interval_minutes=poll_interval)
            )
        else:
            logger.info(
                "GMAIL_TOKEN_FILE not found at %s — email polling disabled", gmail_token_file
            )

        yield

        if scheduler_task is not None:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass

    app = FastAPI(
        title="Finance Agent API",
        description="AI-powered accounts payable assistant backed by QuickBooks Online.",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Middleware: log every request with method, path, status, duration
    @app.middleware("http")
    async def request_logging_middleware(request: Request, call_next) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = round((time.monotonic() - start) * 1000, 1)
        # Extract conversation_id from path if present
        path_parts = request.url.path.split("/")
        conv_id = None
        if "conversations" in path_parts:
            idx = path_parts.index("conversations")
            if idx + 1 < len(path_parts):
                conv_id = path_parts[idx + 1] or None
        logger.info(
            "%s %s %s %sms conv=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            conv_id,
        )
        return response

    register_exception_handlers(app)

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(conversations.router, prefix="/api/v1")
    app.include_router(invoices_router, prefix="/api/v1")

    return app


async def _email_poll_loop(interval_minutes: int) -> None:
    """Background coroutine: poll Gmail for invoice emails on a fixed interval.

    Args:
        interval_minutes: Seconds between each scan cycle.
    """
    interval_seconds = interval_minutes * 60
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            import invoice_scanner
            result = invoice_scanner.scan_emails_for_invoices()
            logger.info(
                "Email poll complete: added=%d skipped=%d",
                result.get("invoices_added", 0),
                result.get("invoices_skipped", 0),
            )
        except Exception:
            logger.exception("Error in background email poll")


app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5001"))
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )
