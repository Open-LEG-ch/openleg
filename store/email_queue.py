# SPDX-License-Identifier: AGPL-3.0-or-later
"""Outbound email queue repository.

Repository module for the outbound email queue domain: schedule emails for the
``scheduled_emails`` table, transition dispatch state (pending/sent/failed/
cancelled), and return queue statistics. The connection seam is resolved via
``database.get_connection`` at call time so existing tests that
``monkeypatch.setattr(database, "get_connection", ...)`` keep working
unchanged and ``database`` can re-export these functions for legacy callers.

Data hygiene (#519): the recipient address is scrubbed the moment its need
ends (sent, cancelled). Delivery failures return the entry to the queue until
``MAX_EMAIL_ATTEMPTS`` is reached, then the entry is terminal. Terminal rows
are deleted by ``cleanup_finished_emails`` after ``EMAIL_QUEUE_RETENTION_DAYS``
(90 days: wide enough for billing disputes and mail-provider bounce
diagnostics; sent and cancelled rows carry no address by then). A queue row
cannot outlive its building (FK ON DELETE CASCADE), and the pending listing
inner-joins buildings, so a row whose building is gone is never handed to the
sender.
"""

import logging

logger = logging.getLogger(__name__)

MAX_EMAIL_ATTEMPTS = 3
EMAIL_QUEUE_RETENTION_DAYS = 90


def _get_connection():
    import database

    return database.get_connection()


# === Email Queue Operations ===


def schedule_email(
    building_id: str, email: str, template_key: str, send_at_timestamp: float
) -> bool:
    """Schedule an email for future delivery."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                # Skip if same template already scheduled/sent for this building
                cur.execute(
                    """
                    SELECT id FROM scheduled_emails
                    WHERE building_id = %s AND template_key = %s AND status IN ('pending', 'sent')
                """,
                    (building_id, template_key),
                )
                if cur.fetchone():
                    return False
                cur.execute(
                    """
                    INSERT INTO scheduled_emails (building_id, email, template_key, send_at)
                    VALUES (%s, %s, %s, to_timestamp(%s))
                """,
                    (building_id, email, template_key, send_at_timestamp),
                )
                return True
    except Exception as e:
        logger.error(f"[DB] Error scheduling email: {e}")
        return False


def get_pending_emails(limit: int = 50) -> list[dict]:
    """Get emails ready to send (send_at <= now, status = pending)."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT se.id, se.building_id, se.email, se.template_key, se.send_at,
                           b.address, b.lat, b.lon, b.plz, b.preferred_locale
                    FROM scheduled_emails se
                    JOIN buildings b ON se.building_id = b.building_id
                    WHERE se.status = 'pending' AND se.send_at <= CURRENT_TIMESTAMP
                    ORDER BY se.send_at ASC
                    LIMIT %s
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting pending emails: {e}")
        return []


def mark_email_sent(email_id: int) -> bool:
    """Mark a scheduled email as sent and scrub its recipient address."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    UPDATE scheduled_emails
                    SET status = 'sent', sent_at = CURRENT_TIMESTAMP, email = %s
                    WHERE id = %s
                """,
                ("", email_id),
            )
            return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[DB] Error marking email sent: {e}")
        return False


def mark_email_failed(email_id: int, error: str) -> bool:
    """Record a delivery failure: back to pending until the attempt bound,
    then terminal."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    UPDATE scheduled_emails
                    SET retry_count = retry_count + 1, error_message = %s
                    WHERE id = %s
                    RETURNING retry_count
                """,
                (error, email_id),
            )
            row = cur.fetchone()
            if row is None:
                return False
            if row["retry_count"] >= MAX_EMAIL_ATTEMPTS:
                cur.execute(
                    """
                        UPDATE scheduled_emails
                        SET status = 'failed'
                        WHERE id = %s
                    """,
                    (email_id,),
                )
            else:
                cur.execute(
                    """
                        UPDATE scheduled_emails
                        SET status = 'pending'
                        WHERE id = %s
                    """,
                    (email_id,),
                )
            return True
    except Exception as e:
        logger.error(f"[DB] Error marking email failed: {e}")
        return False


def cancel_emails_for_building(building_id: str) -> int:
    """Cancel all pending emails for a building (e.g. on unsubscribe) and
    scrub their addresses."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    UPDATE scheduled_emails
                    SET status = 'cancelled', email = %s
                    WHERE building_id = %s AND status = 'pending'
                """,
                ("", building_id),
            )
            return cur.rowcount
    except Exception as e:
        logger.error(f"[DB] Error cancelling emails: {e}")
        return 0


def cleanup_finished_emails(retention_days: int = EMAIL_QUEUE_RETENTION_DAYS) -> int:
    """Delete terminal rows older than the retention horizon."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    DELETE FROM scheduled_emails
                    WHERE status IN ('sent', 'failed', 'cancelled')
                      AND COALESCE(sent_at, created_at)
                          < CURRENT_TIMESTAMP - make_interval(days => %s)
                """,
                (retention_days,),
            )
            return cur.rowcount
    except Exception as e:
        logger.error(f"[DB] Error cleaning up emails: {e}")
        return 0


def get_email_stats() -> dict:
    """Get email queue statistics."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute("""
                    SELECT status, COUNT(*) as count
                    FROM scheduled_emails
                    GROUP BY status
                """)
            stats = {}
            for row in cur.fetchall():
                stats[row["status"]] = row["count"]
            return stats
    except Exception as e:
        logger.error(f"[DB] Error getting email stats: {e}")
        return {}
