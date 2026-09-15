# SPDX-License-Identifier: AGPL-3.0-or-later
"""Persistence for private invoice questions and their append-only history."""

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
                WHERE id = %s AND participant_id = %s AND status = 'issued'
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
    invoice_id: int,
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
        cur.execute(
            f"""
                SELECT * FROM invoice_queries
                WHERE invoice_id = %s AND {field} = %s
                ORDER BY created_at, id
            """,
            (invoice_id, value),
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
        return True
