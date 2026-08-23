# SPDX-License-Identifier: AGPL-3.0-or-later
"""Operations repository.

Owns the LEA job reports and the operational snapshots the admin views read.
"""

import json
import logging

logger = logging.getLogger(__name__)


def _get_connection():
    import database

    return database.get_connection()


def save_lea_report(job_name: str, summary_text: str, status: str = "ok") -> bool:
    """Save an autonomous LEA report from a cron job webhook."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO lea_reports (job_name, summary_text, status)
                    VALUES (%s, %s, %s)
                """,
                (job_name, summary_text, status),
            )
            return True
    except Exception as e:
        logger.error(f"[DB] Error saving LEA report: {e}")
        return False


def get_lea_reports(limit: int = 50) -> list[dict]:
    """Get recent LEA reports, newest first."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT id, job_name, created_at, summary_text, status
                    FROM lea_reports
                    ORDER BY created_at DESC
                    LIMIT %s
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting LEA reports: {e}")
        return []


def save_ops_snapshot(
    source: str,
    category: str,
    summary_text: str = "",
    status: str = "ok",
    payload: dict | None = None,
) -> bool:
    """Save a structured operator snapshot for the admin ops dashboard."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ops_snapshots (source, category, status, summary_text, payload)
                    VALUES (%s, %s, %s, %s, %s::jsonb)
                """,
                    (
                        source,
                        category,
                        status,
                        summary_text,
                        json.dumps(payload or {}),
                    ),
                )
                return True
    except Exception as e:
        logger.error(f"[DB] Error saving ops snapshot: {e}")
        return False


def get_ops_snapshots(
    limit: int = 50,
    source: str | None = None,
    category: str | None = None,
) -> list[dict]:
    """Get structured operator snapshots, newest first."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                where = []
                params: list = []
                if source:
                    where.append("source = %s")
                    params.append(source)
                if category:
                    where.append("category = %s")
                    params.append(category)
                query = """
                    SELECT id, source, category, status, summary_text, payload, created_at
                    FROM ops_snapshots
                """
                if where:
                    query += " WHERE " + " AND ".join(where)
                query += " ORDER BY created_at DESC LIMIT %s"
                params.append(limit)
                cur.execute(query, tuple(params))
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting ops snapshots: {e}")
        return []
