# SPDX-License-Identifier: AGPL-3.0-or-later
"""Persistence for private invoice questions and their append-only history."""

from datetime import datetime

import invoice_queries
from store.operator_api import enqueue_event


def _get_connection():
    import database

    return database.get_connection()


def open_invoice_query(
    invoice_id: int,
    participant_id: str,
    category: str,
    message: str,
    attachment_filename: str = "",
    attachment_data: bytes | None = None,
) -> int | None:
    """Open a case only when the invoice belongs to the participant."""
    category, message = invoice_queries.validate_open(category, message)
    response_due_at, reminder_due_at = invoice_queries.deadlines()
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
                INSERT INTO invoice_queries (
                    invoice_id, community_id, participant_id, category, status,
                    response_due_at, reminder_due_at
                )
                SELECT id, community_id, participant_id, %s, 'open', %s, %s
                FROM invoices
                WHERE id = %s AND participant_id = %s AND status = 'issued'
                RETURNING id
            """,
            (category, response_due_at, reminder_due_at, invoice_id, participant_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        query_id = row["id"]
        _append(
            cur, query_id, participant_id, message, attachment_filename, attachment_data
        )
        _event(cur, query_id, participant_id, None, "open")
        return query_id


def _append(cur, query_id, actor_id, message, filename="", data=None):
    cur.execute(
        """
            INSERT INTO invoice_query_messages (
                query_id, actor_id, message, attachment_filename, attachment_data
            ) VALUES (%s, %s, %s, %s, %s)
        """,
        (query_id, actor_id, message, filename or None, data),
    )


def _event(cur, query_id, actor_id, previous_status, new_status, linked_reference=None):
    cur.execute(
        """
            INSERT INTO invoice_query_events (
                query_id, actor_id, previous_status, new_status, linked_reference
            ) VALUES (%s, %s, %s, %s, %s)
        """,
        (query_id, actor_id, previous_status, new_status, linked_reference),
    )


def record_invoice_query_linkage(cur, invoice_id, actor_id, reference):
    """Append one correction-linkage event to every open query on the invoice.

    Runs inside the caller's invoice transaction so the case history records
    the correction exactly when the invoice lifecycle does.
    """
    cur.execute(
        """
            SELECT id, status FROM invoice_queries
            WHERE invoice_id = %s AND status IN ('open', 'acknowledged')
        """,
        (invoice_id,),
    )
    for row in cur.fetchall():
        _event(cur, row["id"], actor_id, row["status"], row["status"], reference)


def list_invoice_queries(
    invoice_id: int | None,
    *,
    participant_id: str | None = None,
    community_id: str | None = None,
) -> list[dict]:
    """List cases through either the owner or community-operator scope."""
    if bool(participant_id) == bool(community_id):
        raise ValueError("Exactly one invoice-query scope is required.")
    field, value = (
        ("participant_id", participant_id)
        if participant_id
        else ("community_id", community_id)
    )
    with _get_connection() as conn, conn.cursor() as cur:
        invoice_filter = "invoice_id = %s AND " if invoice_id is not None else ""
        params = (invoice_id, value) if invoice_id is not None else (value,)
        cur.execute(
            f"""
                SELECT * FROM invoice_queries
                WHERE {invoice_filter}{field} = %s
                ORDER BY created_at, id
            """,
            params,
        )
        cases = [dict(row) for row in cur.fetchall()]
        for case in cases:
            cur.execute(
                """
                    SELECT id, actor_id, message, attachment_filename, created_at
                    FROM invoice_query_messages WHERE query_id = %s ORDER BY id
                """,
                (case["id"],),
            )
            case["messages"] = [dict(row) for row in cur.fetchall()]
            cur.execute(
                "SELECT * FROM invoice_query_events WHERE query_id = %s ORDER BY id",
                (case["id"],),
            )
            case["events"] = [dict(row) for row in cur.fetchall()]
        return cases


def add_invoice_query_message(
    query_id: int,
    actor_id: str,
    message: str,
    *,
    participant_id: str | None = None,
    community_id: str | None = None,
    attachment_filename: str = "",
    attachment_data: bytes | None = None,
) -> bool:
    """Append a message after checking participant or operator scope."""
    message = invoice_queries.validate_message(message)
    if bool(participant_id) == bool(community_id):
        return False
    field, value = (
        ("participant_id", participant_id)
        if participant_id
        else ("community_id", community_id)
    )
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT id FROM invoice_queries WHERE id = %s AND {field} = %s",
            (query_id, value),
        )
        if not cur.fetchone():
            return False
        _append(cur, query_id, actor_id, message, attachment_filename, attachment_data)
        return True


def transition_invoice_query(
    query_id: int, community_id: str, actor_id: str, target_status: str
) -> bool:
    """Lock, validate and record an operator status transition."""
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
                SELECT status FROM invoice_queries
                WHERE id = %s AND community_id = %s FOR UPDATE
            """,
            (query_id, community_id),
        )
        row = cur.fetchone()
        if not row:
            return False
        invoice_queries.require_transition(row["status"], target_status)
        cur.execute(
            "UPDATE invoice_queries SET status = %s, updated_at = NOW() WHERE id = %s",
            (target_status, query_id),
        )
        _event(cur, query_id, actor_id, row["status"], target_status)
        enqueue_event(
            cur,
            "invoice.case.updated",
            str(query_id),
            community_id,
            {"status": target_status},
        )
        return True


def due_invoice_query_reminders(now: datetime) -> list[dict]:
    """List open or acknowledged cases whose reminder deadline has passed."""
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
                SELECT id, community_id, invoice_id, status, response_due_at
                FROM invoice_queries
                WHERE status IN ('open', 'acknowledged')
                  AND reminder_due_at IS NOT NULL
                  AND reminder_due_at <= %s
                  AND reminder_sent_at IS NULL
                ORDER BY created_at, id
            """,
            (now,),
        )
        return [dict(row) for row in cur.fetchall()]


def mark_invoice_query_reminded(case_id: int, actor: str) -> bool:
    """Record the overdue reminder once; repeat calls write nothing."""
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
                UPDATE invoice_queries SET reminder_sent_at = CURRENT_TIMESTAMP
                WHERE id = %s AND reminder_sent_at IS NULL
                RETURNING status
            """,
            (case_id,),
        )
        row = cur.fetchone()
        if not row:
            return False
        _event(cur, case_id, actor, row["status"], row["status"])
        return True


def update_invoice_query(
    query_id: int,
    community_id: str,
    actor_id: str,
    *,
    message: str = "",
    target_status: str = "",
) -> bool:
    """Apply an operator reply and status change in one transaction."""
    validated_message = invoice_queries.validate_message(message) if message else ""
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
                SELECT status FROM invoice_queries
                WHERE id = %s AND community_id = %s FOR UPDATE
            """,
            (query_id, community_id),
        )
        row = cur.fetchone()
        if not row:
            return False
        if target_status:
            invoice_queries.require_transition(row["status"], target_status)
        if validated_message:
            _append(cur, query_id, actor_id, validated_message)
        if target_status:
            cur.execute(
                "UPDATE invoice_queries SET status = %s, updated_at = NOW() WHERE id = %s",
                (target_status, query_id),
            )
            _event(cur, query_id, actor_id, row["status"], target_status)
            enqueue_event(
                cur,
                "invoice.case.updated",
                str(query_id),
                community_id,
                {"status": target_status},
            )
        return True
