# SPDX-License-Identifier: AGPL-3.0-or-later
"""Persistence for private invoice questions and their append-only history."""

from collections import defaultdict
from datetime import datetime

from psycopg2.extras import Json

import invoice_queries
import operator_actor
from store.invoice_identity import REGISTRATION_MATCH_SQL
from store.operator_api import enqueue_event
from store.operator_operations import _remember, _replay, request_hash

_MEMBER_REGISTRATION_SQL = f"""
    AND EXISTS (
        SELECT 1 FROM invoices i JOIN buildings b ON b.building_id=i.participant_id
        WHERE i.id=q.invoice_id AND i.participant_id=q.participant_id
          AND {REGISTRATION_MATCH_SQL}
    )
"""


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
            f"""
                INSERT INTO invoice_queries (
                    invoice_id, community_id, participant_id, category, status,
                    response_due_at, reminder_due_at
                )
                SELECT i.id, i.community_id, i.participant_id, %s, 'open', %s, %s
                FROM invoices i JOIN buildings b ON b.building_id=i.participant_id
                WHERE i.id = %s AND i.participant_id = %s AND i.status = 'issued'
                  AND {REGISTRATION_MATCH_SQL}
                FOR KEY SHARE OF b
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
                query_id, actor_id, message, attachment_filename, attachment_data, actor
            ) VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            query_id,
            actor_id,
            message,
            filename or None,
            data,
            Json(operator_actor.snapshot()),
        ),
    )


def _event(cur, query_id, actor_id, previous_status, new_status, linked_reference=None):
    cur.execute(
        """
            INSERT INTO invoice_query_events (
                query_id, actor_id, previous_status, new_status, linked_reference, actor
            ) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (
            query_id,
            actor_id,
            previous_status,
            new_status,
            linked_reference,
            Json(operator_actor.snapshot()),
        ),
    )
    return cur.fetchone()["id"]


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
        invoice_filter = "q.invoice_id = %s AND " if invoice_id is not None else ""
        registration_filter = _MEMBER_REGISTRATION_SQL if participant_id else ""
        params = (invoice_id, value) if invoice_id is not None else (value,)
        cur.execute(
            f"""
                SELECT q.* FROM invoice_queries q
                WHERE {invoice_filter}q.{field} = %s {registration_filter}
                ORDER BY q.created_at, q.id
            """,
            params,
        )
        cases = [dict(row) for row in cur.fetchall()]
        if not cases:
            return []
        query_ids = [case["id"] for case in cases]
        cur.execute(
            """
                SELECT query_id, id, actor_id, actor, message, attachment_filename, created_at
                FROM invoice_query_messages
                WHERE query_id = ANY(%s) ORDER BY query_id, id
            """,
            (query_ids,),
        )
        messages = defaultdict(list)
        for row in cur.fetchall():
            row = dict(row)
            messages[row.pop("query_id")].append(row)
        cur.execute(
            """
                SELECT * FROM invoice_query_events
                WHERE query_id = ANY(%s) ORDER BY query_id, id
            """,
            (query_ids,),
        )
        events = defaultdict(list)
        for row in cur.fetchall():
            row = dict(row)
            events[row["query_id"]].append(row)
        for case in cases:
            case["messages"] = messages[case["id"]]
            case["events"] = events[case["id"]]
        return cases


def get_invoice_query_attachment(message_id, *, participant_id=None, community_id=None):
    """Read one attachment only through its case owner or operator scope."""
    if bool(participant_id) == bool(community_id):
        raise ValueError("Exactly one invoice-query scope is required.")
    field, value = (
        ("participant_id", participant_id)
        if participant_id
        else ("community_id", community_id)
    )
    with _get_connection() as conn, conn.cursor() as cur:
        registration_filter = _MEMBER_REGISTRATION_SQL if participant_id else ""
        cur.execute(
            f"""SELECT m.attachment_filename,m.attachment_data
                FROM invoice_query_messages m JOIN invoice_queries q ON q.id=m.query_id
                WHERE m.id=%s AND q.{field}=%s AND m.attachment_data IS NOT NULL
                  {registration_filter}""",
            (message_id, value),
        )
        row = cur.fetchone()
        return dict(row) if row else None


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
        registration_filter = _MEMBER_REGISTRATION_SQL if participant_id else ""
        cur.execute(
            f"""SELECT q.id FROM invoice_queries q
                WHERE q.id = %s AND q.{field} = %s {registration_filter}""",
            (query_id, value),
        )
        if not cur.fetchone():
            return False
        _append(cur, query_id, actor_id, message, attachment_filename, attachment_data)
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
        return (
            _change(
                cur,
                query_id,
                community_id,
                actor_id,
                validated_message or None,
                target_status or None,
            )
            is not None
        )


def _change(
    cur, query_id, community_id, actor_id, message, target_status, *, dry_run=False
):
    """Own the scoped lock, optional reply, audit identity and event atomically."""
    cur.execute(
        "SELECT status FROM invoice_queries WHERE id=%s AND community_id=%s FOR UPDATE",
        (query_id, community_id),
    )
    row = cur.fetchone()
    if not row:
        return None
    if target_status is not None:
        invoice_queries.require_transition(row["status"], target_status)
    if message is not None:
        message = invoice_queries.validate_message(message)
    if dry_run:
        return {
            "id": query_id,
            "previous_status": row["status"],
            "status": target_status,
            "dry_run": True,
        }
    if message is not None:
        _append(cur, query_id, actor_id, message)
    event_id = None
    if target_status is not None:
        cur.execute(
            "UPDATE invoice_queries SET status=%s,updated_at=NOW() WHERE id=%s",
            (target_status, query_id),
        )
        transition_id = _event(cur, query_id, actor_id, row["status"], target_status)
        event_id = enqueue_event(
            cur,
            "invoice.case.updated",
            str(query_id),
            community_id,
            {"status": target_status},
            event_identity=f"transition:{transition_id}",
        )
    return {
        "id": query_id,
        "status": target_status or row["status"],
        "event_id": event_id,
    }


def respond_operator_invoice_case(
    case_id, community_id, actor_id, message, status, key, *, dry_run=False
):
    """Apply a keyed operator response and retain its request-bound replay."""
    action = f"case.respond:{case_id}"
    fingerprint = request_hash({"message": message, "status": status})
    with _get_connection() as conn, conn.cursor() as cur:
        if dry_run:
            return _change(
                cur,
                case_id,
                community_id,
                actor_id,
                message or "",
                status or "",
                dry_run=True,
            )
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"{community_id}:{action}:{key}",),
        )
        replay = _replay(cur, community_id, action, key, fingerprint)
        if replay:
            return replay
        response = _change(
            cur, case_id, community_id, actor_id, message or "", status or ""
        )
        if response is None:
            return None
        response["replayed"] = False
        _remember(cur, community_id, action, key, fingerprint, response)
        return response
