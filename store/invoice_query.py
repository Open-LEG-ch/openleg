# SPDX-License-Identifier: AGPL-3.0-or-later
"""Persistence for private invoice questions and their append-only history."""

from collections import defaultdict

import invoice_queries


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
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
                INSERT INTO invoice_queries (
                    invoice_id, community_id, participant_id, category, status
                )
                SELECT id, community_id, participant_id, %s, 'open'
                FROM invoices
                WHERE id = %s AND participant_id = %s
                  AND status IN ('issued', 'delivered', 'paid')
                RETURNING id
            """,
            (category, invoice_id, participant_id),
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


def _event(cur, query_id, actor_id, previous_status, new_status):
    cur.execute(
        """
            INSERT INTO invoice_query_events (
                query_id, actor_id, previous_status, new_status
            ) VALUES (%s, %s, %s, %s)
        """,
        (query_id, actor_id, previous_status, new_status),
    )


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
        if not cases:
            return []
        query_ids = [case["id"] for case in cases]
        cur.execute(
            """
                SELECT query_id, id, actor_id, message, attachment_filename, created_at
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
        return True
