"""
Invoice scanning business logic for the Finance Agent.

Orchestrates the full email → parse → queue → QBO Bill pipeline.
Called by the tool dispatcher in tools.py and by the FastAPI invoices router.

All state (processed invoices) is stored in the SQLite ``email_invoices`` table.
The database path is resolved from the DB_PATH environment variable.
"""

import base64
import datetime
import json
import logging
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

import qbo_client

load_dotenv()

logger = logging.getLogger(__name__)

_DB_PATH = Path(os.getenv("DB_PATH", "data/conversations.db"))


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _get_conn() -> sqlite3.Connection:
    """Open a SQLite connection to the invoice database."""
    if str(_DB_PATH) == ":memory:":
        conn = sqlite3.connect(
            "file::memory:?cache=shared", uri=True, check_same_thread=False
        )
    else:
        conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _upsert_email_invoice(
    conn: sqlite3.Connection,
    email_id: str,
    subject: str,
    from_address: str,
    received_at: str,
    attachment_name: str,
    extracted_data: dict,
    vendor_id: str | None,
    vendor_name: str | None,
) -> str:
    """Insert an email invoice row, skipping if email_id already exists.

    Returns:
        The invoice queue ID (new or existing).
    """
    existing = conn.execute(
        "SELECT id FROM email_invoices WHERE email_id = ?", (email_id,)
    ).fetchone()
    if existing:
        return existing["id"]

    inv_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO email_invoices
            (id, email_id, subject, from_address, received_at, attachment_name,
             status, extracted_data, vendor_id, vendor_name, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
        """,
        (
            inv_id,
            email_id,
            subject,
            from_address,
            received_at,
            attachment_name,
            json.dumps(extracted_data),
            vendor_id,
            vendor_name,
            _now(),
        ),
    )
    return inv_id


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan_emails_for_invoices(max_emails: int = 20) -> dict[str, Any]:
    """Scan Gmail inbox for invoice emails, parse them, and add to the queue.

    Args:
        max_emails: Maximum number of emails to process per run.

    Returns:
        Dict with keys: emails_scanned, invoices_found, invoices_added,
        invoices_skipped (already in queue), items (list of queue summaries).
    """
    import email_client
    import invoice_parser

    emails = email_client.fetch_invoice_emails(max_results=max_emails)
    logger.info("scan_emails_for_invoices: %d candidate emails", len(emails))

    invoices_added = 0
    invoices_skipped = 0
    items: list[dict] = []

    conn = _get_conn()
    try:
        for email in emails:
            msg_id = email["message_id"]
            subject = email["subject"]
            from_addr = email["from_address"]
            received_at = email["received_at"]

            # Check if already queued
            existing = conn.execute(
                "SELECT id, status FROM email_invoices WHERE email_id = ?", (msg_id,)
            ).fetchone()
            if existing:
                invoices_skipped += 1
                continue

            # Parse attachments first; fall back to email body
            parsed: dict | None = None
            attachment_name = ""

            if email["has_attachment"]:
                attachments = email_client.get_attachments(msg_id)
                for att in attachments:
                    att_data = base64.b64decode(att["data_b64"] + "==")
                    parsed = invoice_parser.parse_invoice(
                        content=att_data,
                        mime_type=att["mime_type"],
                        source_email=from_addr,
                    )
                    attachment_name = att["filename"]
                    if parsed.get("total_amount", 0) > 0:
                        break  # Use first successfully parsed attachment

            if parsed is None or parsed.get("total_amount", 0) == 0:
                # Fall back to email body text
                body_text = email_client.get_email_body(msg_id)
                if body_text.strip():
                    parsed = invoice_parser.parse_invoice(
                        content=body_text,
                        mime_type="text/plain",
                        source_email=from_addr,
                    )

            if parsed is None:
                parsed = {}

            # Vendor matching
            vendor = None
            vendor_name = parsed.get("vendor_name")
            if vendor_name:
                vendor = qbo_client.find_vendor_by_name(vendor_name)

            vendor_id = vendor["Id"] if vendor else None
            matched_vendor_name = vendor.get("DisplayName") if vendor else vendor_name

            inv_id = _upsert_email_invoice(
                conn=conn,
                email_id=msg_id,
                subject=subject,
                from_address=from_addr,
                received_at=received_at,
                attachment_name=attachment_name,
                extracted_data=parsed,
                vendor_id=vendor_id,
                vendor_name=matched_vendor_name,
            )
            conn.commit()

            # Mark email as processed so it won't be re-fetched
            email_client.mark_as_processed(msg_id)
            invoices_added += 1

            items.append({
                "id": inv_id,
                "subject": subject,
                "from": from_addr,
                "vendor_name": matched_vendor_name,
                "total_amount": parsed.get("total_amount"),
                "due_date": parsed.get("due_date"),
                "vendor_matched": vendor is not None,
                "status": "pending",
            })

    finally:
        conn.close()

    return {
        "emails_scanned": len(emails),
        "invoices_found": invoices_added + invoices_skipped,
        "invoices_added": invoices_added,
        "invoices_skipped": invoices_skipped,
        "items": items,
    }


def get_invoice_queue(status: str | None = None) -> list[dict]:
    """Return invoices from the queue, optionally filtered by status.

    Args:
        status: One of 'pending', 'approved', 'rejected', 'created'. None = all.

    Returns:
        List of invoice summary dicts.
    """
    conn = _get_conn()
    try:
        if status:
            rows = conn.execute(
                """
                SELECT id, email_id, subject, from_address, received_at,
                       attachment_name, status, extracted_data, vendor_id,
                       vendor_name, bill_id, created_at
                FROM email_invoices
                WHERE status = ?
                ORDER BY created_at DESC
                """,
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, email_id, subject, from_address, received_at,
                       attachment_name, status, extracted_data, vendor_id,
                       vendor_name, bill_id, created_at
                FROM email_invoices
                ORDER BY created_at DESC
                """,
            ).fetchall()
    finally:
        conn.close()

    results = []
    for row in rows:
        extracted = json.loads(row["extracted_data"] or "{}")
        results.append({
            "id": row["id"],
            "subject": row["subject"],
            "from_address": row["from_address"],
            "received_at": row["received_at"],
            "attachment_name": row["attachment_name"],
            "status": row["status"],
            "vendor_name": row["vendor_name"],
            "vendor_id": row["vendor_id"],
            "vendor_matched": row["vendor_id"] is not None,
            "invoice_number": extracted.get("invoice_number"),
            "invoice_date": extracted.get("invoice_date"),
            "due_date": extracted.get("due_date"),
            "total_amount": extracted.get("total_amount"),
            "currency": extracted.get("currency", "USD"),
            "line_items": extracted.get("line_items", []),
            "bill_id": row["bill_id"],
            "created_at": row["created_at"],
        })
    return results


def approve_invoice(
    invoice_queue_id: str,
    expense_account_id: str,
    user_confirmed: bool,
) -> dict[str, Any]:
    """Approve an invoice and optionally create a QBO Bill.

    If user_confirmed is False, returns a preview without creating the bill.
    If the vendor is not in QBO, returns an error asking the user to add them.

    Args:
        invoice_queue_id: ID from the email_invoices table.
        expense_account_id: QBO Account ID for expense categorization.
        user_confirmed: True to create the bill; False to preview only.

    Returns:
        Dict with status, bill details (or preview), and action taken.
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM email_invoices WHERE id = ?", (invoice_queue_id,)
        ).fetchone()

        if row is None:
            return {"error": f"Invoice queue ID '{invoice_queue_id}' not found."}

        if row["status"] == "created":
            return {"error": f"Invoice already processed. QBO Bill ID: {row['bill_id']}"}

        if row["status"] == "rejected":
            return {"error": "Invoice was previously rejected."}

        if row["vendor_id"] is None:
            vendor_name = row["vendor_name"] or "Unknown"
            return {
                "error": (
                    f"Vendor '{vendor_name}' is not in your QuickBooks system. "
                    "Please add the vendor in QBO first, then retry."
                ),
                "vendor_name": vendor_name,
                "action_required": "add_vendor",
            }

        extracted = json.loads(row["extracted_data"] or "{}")
        line_items = extracted.get("line_items") or []
        total_amount = extracted.get("total_amount", 0)

        # Use total as single line if no line items extracted
        if not line_items and total_amount:
            line_items = [{"description": row["subject"] or "Invoice", "amount": total_amount}]

        due_date = extracted.get("due_date") or datetime.date.today().isoformat()

        preview = {
            "invoice_queue_id": invoice_queue_id,
            "vendor_name": row["vendor_name"],
            "vendor_id": row["vendor_id"],
            "invoice_number": extracted.get("invoice_number"),
            "invoice_date": extracted.get("invoice_date"),
            "due_date": due_date,
            "line_items": line_items,
            "total_amount": total_amount,
            "currency": extracted.get("currency", "USD"),
            "expense_account_id": expense_account_id,
        }

        if not user_confirmed:
            preview["status"] = "preview"
            preview["message"] = (
                "Review the details above and call approve_invoice again "
                "with user_confirmed=true to create the bill in QuickBooks."
            )
            return preview

        # Create the bill
        bill = qbo_client.create_bill(
            vendor_id=row["vendor_id"],
            line_items=line_items,
            due_date=due_date,
            expense_account_id=expense_account_id,
            invoice_number=extracted.get("invoice_number", ""),
            memo=f"Imported from email: {row['subject']}",
        )

        bill_id = bill.get("Id")
        conn.execute(
            "UPDATE email_invoices SET status = 'created', bill_id = ? WHERE id = ?",
            (bill_id, invoice_queue_id),
        )
        conn.commit()

        logger.info(
            "Invoice %s approved: QBO bill_id=%s vendor=%s total=%s",
            invoice_queue_id,
            bill_id,
            row["vendor_name"],
            total_amount,
        )

        return {
            "status": "created",
            "bill_id": bill_id,
            "vendor_name": row["vendor_name"],
            "total_amount": total_amount,
            "due_date": due_date,
            "message": f"Bill #{bill_id} created in QuickBooks for {row['vendor_name']}.",
        }
    finally:
        conn.close()


def reject_invoice(invoice_queue_id: str, reason: str = "") -> dict[str, Any]:
    """Mark an invoice as rejected so no bill will be created.

    Args:
        invoice_queue_id: ID from the email_invoices table.
        reason: Optional rejection reason stored for audit purposes.

    Returns:
        Dict with status and message.
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id, status, vendor_name FROM email_invoices WHERE id = ?",
            (invoice_queue_id,),
        ).fetchone()

        if row is None:
            return {"error": f"Invoice queue ID '{invoice_queue_id}' not found."}

        if row["status"] == "created":
            return {"error": "Cannot reject an invoice that has already been converted to a bill."}

        memo = json.dumps({"rejected_reason": reason}) if reason else None
        conn.execute(
            "UPDATE email_invoices SET status = 'rejected', extracted_data = CASE WHEN ? IS NOT NULL THEN json_patch(extracted_data, ?) ELSE extracted_data END WHERE id = ?",
            (memo, memo, invoice_queue_id),
        )
        conn.commit()

        logger.info("Invoice %s rejected. Reason: %s", invoice_queue_id, reason)
        return {
            "status": "rejected",
            "invoice_queue_id": invoice_queue_id,
            "message": f"Invoice from '{row['vendor_name']}' has been rejected.",
        }
    finally:
        conn.close()
