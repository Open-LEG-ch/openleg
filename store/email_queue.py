# SPDX-License-Identifier: AGPL-3.0-or-later
"""Outbound email queue repository.

Repository module for the outbound email queue domain: schedule emails for the
``scheduled_emails`` table, transition dispatch state (pending/sent/failed/
cancelled), and return queue statistics. The connection seam is resolved via
``database.get_connection`` at call time so existing tests that
``monkeypatch.setattr(database, "get_connection", ...)`` keep working
unchanged and ``database`` can re-export these functions for legacy callers.
"""

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


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


def get_pending_emails(limit: int = 50) -> List[Dict]:
    """Get emails ready to send (send_at <= now, status = pending)."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT se.id, se.building_id, se.email, se.template_key, se.send_at,
                           b.address, b.lat, b.lon, b.plz
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
    """Mark a scheduled email as sent."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE scheduled_emails
                    SET status = 'sent', sent_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """,
                    (email_id,),
                )
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[DB] Error marking email sent: {e}")
        return False


def mark_email_failed(email_id: int, error: str) -> bool:
    """Mark a scheduled email as failed."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE scheduled_emails
                    SET status = 'failed', error_message = %s
                    WHERE id = %s
                """,
                    (error, email_id),
                )
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[DB] Error marking email failed: {e}")
        return False


def cancel_emails_for_building(building_id: str) -> int:
    """Cancel all pending emails for a building (e.g. on unsubscribe)."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE scheduled_emails
                    SET status = 'cancelled'
                    WHERE building_id = %s AND status = 'pending'
                """,
                    (building_id,),
                )
                return cur.rowcount
    except Exception as e:
        logger.error(f"[DB] Error cancelling emails: {e}")
        return 0


def get_email_stats() -> Dict:
    """Get email queue statistics."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
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
