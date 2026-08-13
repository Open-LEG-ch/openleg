# SPDX-License-Identifier: AGPL-3.0-or-later
"""Correspondence ledger repository.

Repository module for the community correspondence domain: one shared log
per LEG of outgoing and incoming mail (email and physical post), manually
logged by members. Phase 6 MVP of ``docs/leg-registry.md`` — deliberately
no external mail provider; a hybrid-mail integration is a separate,
explicit business decision. The connection seam is resolved via
``database.get_connection`` at call time so existing tests that
``monkeypatch.setattr(database, "get_connection", ...)`` keep working
unchanged and ``database`` can re-export these functions for legacy callers.
"""

import logging

logger = logging.getLogger(__name__)

DIRECTIONS = {"in", "out"}
CHANNELS = {"email", "post"}


def _get_connection():
    import database

    return database.get_connection()


def log_correspondence(
    community_id: str,
    direction: str,
    channel: str,
    counterparty: str,
    subject: str,
    notes: str = "",
    logged_by: str = "",
    attachment_filename: str = "",
    attachment_mime: str = "",
    attachment_data: bytes | None = None,
) -> int | None:
    """Append one ledger entry. Returns the row id, or None on invalid input."""
    if direction not in DIRECTIONS or channel not in CHANNELS:
        return None
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO correspondence_log (
                        community_id, direction, channel, counterparty,
                        subject, notes, logged_by, attachment_filename,
                        attachment_mime, attachment_data
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """,
                (
                    community_id,
                    direction,
                    channel,
                    counterparty,
                    subject,
                    notes,
                    logged_by,
                    attachment_filename or None,
                    attachment_mime or None,
                    attachment_data,
                ),
            )
            row = cur.fetchone()
            return dict(row)["id"] if row else None
    except Exception as e:
        logger.error(f"[DB] Error logging correspondence: {e}")
        return None


def list_correspondence(community_id: str, limit: int = 100) -> list[dict]:
    """List a community's ledger entries, newest first."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT * FROM correspondence_log
                    WHERE community_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                """,
                (community_id, max(1, min(int(limit), 500))),
            )
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error listing correspondence: {e}")
        return []


def get_correspondence_attachment(entry_id: int, community_id: str) -> dict | None:
    """Return one attachment scoped to its community."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT id, community_id, attachment_filename,
                           attachment_mime, attachment_data
                    FROM correspondence_log
                    WHERE id = %s AND community_id = %s
                      AND attachment_data IS NOT NULL
                """,
                (entry_id, community_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting correspondence attachment: {e}")
        return None
