"""
Invoice ingestion endpoints for the Finance Agent API.

Provides REST endpoints for the email → invoice → QBO Bill pipeline.
These endpoints mirror the agent tool capabilities so that the frontend
can also drive the workflow directly without going through the chat agent.
"""

import logging
import sqlite3
import sys
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import api.db as db_module
from api.auth import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/invoices",
    tags=["invoices"],
    dependencies=[Depends(verify_api_key)],
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ScanRequest(BaseModel):
    """Request body for POST /invoices/scan."""

    max_emails: int = 20


class ApproveRequest(BaseModel):
    """Request body for POST /invoices/{id}/approve."""

    expense_account_id: str
    user_confirmed: bool = False


class RejectRequest(BaseModel):
    """Request body for POST /invoices/{id}/reject."""

    reason: str = ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/scan")
async def scan_invoices(body: ScanRequest) -> dict:
    """Trigger a Gmail inbox scan for invoice emails.

    Fetches unread emails, parses invoice attachments / body text using
    Claude's document API, matches vendors against QBO, and adds results
    to the invoice review queue.

    Returns:
        Summary with counts and list of newly queued invoices.
    """
    try:
        import invoice_scanner
        return invoice_scanner.scan_emails_for_invoices(max_emails=body.max_emails)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "GMAIL_NOT_CONFIGURED",
                "message": str(exc),
                "recoverable": False,
            },
        ) from exc
    except Exception as exc:
        logger.exception("Error scanning emails")
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "SCAN_ERROR",
                "message": str(exc),
                "recoverable": True,
            },
        ) from exc


@router.get("/queue")
async def get_queue(status: str | None = None) -> list[dict]:
    """List invoices in the review queue.

    Args:
        status: Optional filter — 'pending', 'approved', 'rejected', 'created'.

    Returns:
        List of invoice queue entries with extracted fields and vendor match info.
    """
    import invoice_scanner
    return invoice_scanner.get_invoice_queue(status=status)


@router.post("/{invoice_id}/approve")
async def approve_invoice(invoice_id: str, body: ApproveRequest) -> dict:
    """Approve an invoice and optionally create a Bill in QBO.

    When ``user_confirmed`` is False, returns a preview without side effects.
    When True, creates the QBO Bill and marks the invoice as 'created'.

    Args:
        invoice_id: Invoice queue ID.
        body.expense_account_id: QBO Account ID for expense categorization.
        body.user_confirmed: Must be true to actually create the bill.

    Returns:
        Preview dict (if not confirmed) or created bill details.
    """
    import invoice_scanner
    result = invoice_scanner.approve_invoice(
        invoice_queue_id=invoice_id,
        expense_account_id=body.expense_account_id,
        user_confirmed=body.user_confirmed,
    )
    if "error" in result:
        status_code = 404 if "not found" in result["error"].lower() else 400
        raise HTTPException(status_code=status_code, detail=result)
    return result


@router.post("/{invoice_id}/reject")
async def reject_invoice(invoice_id: str, body: RejectRequest) -> dict:
    """Mark an invoice as rejected.

    Args:
        invoice_id: Invoice queue ID.
        body.reason: Optional rejection reason.

    Returns:
        Confirmation dict.
    """
    import invoice_scanner
    result = invoice_scanner.reject_invoice(
        invoice_queue_id=invoice_id,
        reason=body.reason,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result)
    return result
