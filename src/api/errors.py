"""
Error envelope helpers and global exception handlers for the Finance Agent API.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def api_error(
    status: int,
    code: str,
    message: str,
    recoverable: bool,
) -> JSONResponse:
    """Build a standard error envelope JSON response.

    Args:
        status: HTTP status code.
        code: Machine-readable error code string.
        message: Human-readable description.
        recoverable: True if the client can meaningfully retry.

    Returns:
        A ``JSONResponse`` with the standard envelope body.
    """
    return JSONResponse(
        status_code=status,
        content={
            "status": "error",
            "error_code": code,
            "message": message,
            "recoverable": recoverable,
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach global exception handlers to the FastAPI application.

    Args:
        app: The FastAPI application instance.
    """

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return api_error(
            status=400,
            code="VALIDATION_ERROR",
            message=f"Invalid request: {exc.errors()[0]['msg']}",
            recoverable=True,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("Unhandled exception on %s %s", request.method, request.url)
        return api_error(
            status=500,
            code="INTERNAL_ERROR",
            message="An unexpected error occurred. Please try again later.",
            recoverable=True,
        )
