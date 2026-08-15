# SPDX-License-Identifier: AGPL-3.0-or-later
"""Referral repository.

Repository module for the referral domain. The connection seam is resolved
via ``database.get_connection`` at call time so existing tests that
``monkeypatch.setattr(database, "get_connection", ...)`` keep working unchanged
and ``database`` can re-export these functions for legacy callers.
"""

import logging

logger = logging.getLogger(__name__)


def _get_connection():
    import database

    return database.get_connection()


def get_referral_code(building_id: str) -> str | None:
    """Get the referral code for a building."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT referral_code FROM buildings WHERE building_id = %s
                """,
                (building_id,),
            )
            row = cur.fetchone()
            if row:
                return row["referral_code"]
            return None
    except Exception as e:
        logger.error(f"[DB] Error getting referral code: {e}")
        return None


def get_building_by_referral_code(code: str) -> dict | None:
    """Find a building by its referral code."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT building_id, email, address FROM buildings
                    WHERE referral_code = %s
                """,
                (code,),
            )
            row = cur.fetchone()
            if row:
                return dict(row)
            return None
    except Exception as e:
        logger.error(f"[DB] Error finding building by referral code: {e}")
        return None


def get_referral_stats(building_id: str) -> dict:
    """Get referral statistics for a building."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT COUNT(*) as total_referrals
                    FROM referrals WHERE referrer_id = %s
                """,
                (building_id,),
            )
            row = cur.fetchone()
            return {"total_referrals": row["total_referrals"] if row else 0}
    except Exception as e:
        logger.error(f"[DB] Error getting referral stats: {e}")
        return {"total_referrals": 0}


def get_referral_leaderboard(limit: int = 10, city_id: str | None = None) -> list[dict]:
    """Get top referrers, optionally scoped by city_id."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            if city_id:
                cur.execute(
                    """
                        SELECT b.building_id,
                               SPLIT_PART(b.address, ',', 1) as street,
                               COUNT(r.id) as referral_count
                        FROM buildings b
                        JOIN referrals r ON b.building_id = r.referrer_id
                        INNER JOIN consents c ON b.building_id = c.building_id
                        WHERE b.city_id = %s
                        AND c.share_with_neighbors = TRUE
                        GROUP BY b.building_id, b.address
                        ORDER BY referral_count DESC
                        LIMIT %s
                    """,
                    (city_id, limit),
                )
            else:
                cur.execute(
                    """
                        SELECT b.building_id,
                               SPLIT_PART(b.address, ',', 1) as street,
                               COUNT(r.id) as referral_count
                        FROM buildings b
                        JOIN referrals r ON b.building_id = r.referrer_id
                        INNER JOIN consents c ON b.building_id = c.building_id
                        WHERE c.share_with_neighbors = TRUE
                        GROUP BY b.building_id, b.address
                        ORDER BY referral_count DESC
                        LIMIT %s
                    """,
                    (limit,),
                )
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting leaderboard: {e}")
        return []
