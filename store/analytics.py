# SPDX-License-Identifier: AGPL-3.0-or-later
"""Analytics event repository.

Owns the raw event log and the aggregate counts the dashboards read.
"""

import logging

logger = logging.getLogger(__name__)


def _get_connection():
    import database

    return database.get_connection()


def track_event(
    event_type: str, building_id: str | None = None, data: dict | None = None
) -> bool:
    """Track an analytics event."""
    try:
        import json

        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO analytics_events (event_type, building_id, data)
                    VALUES (%s, %s, %s)
                """,
                (
                    event_type,
                    building_id or "",
                    json.dumps(data if data is not None else {}),
                ),
            )
            return True
    except Exception as e:
        logger.error(f"[DB] Error tracking event: {e}")
        return False


def get_stats(city_id: str | None = None) -> dict:
    """Get platform statistics, optionally scoped by city_id."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                stats = {}
                city_filter = " AND city_id = %s" if city_id else ""
                city_params = (city_id,) if city_id else ()

                # Total buildings
                cur.execute(
                    f"SELECT COUNT(*) as count FROM buildings WHERE verified = TRUE{city_filter}",
                    city_params,
                )
                stats["total_buildings"] = cur.fetchone()["count"]

                # By type
                cur.execute(
                    f"""
                    SELECT user_type, COUNT(*) as count
                    FROM buildings WHERE verified = TRUE{city_filter}
                    GROUP BY user_type
                """,
                    city_params,
                )
                for row in cur.fetchall():
                    stats[f"{row['user_type']}_count"] = row["count"]

                # Total referrals
                if city_id:
                    cur.execute(
                        """
                        SELECT COUNT(*) as count FROM referrals r
                        JOIN buildings b ON r.referrer_id = b.building_id
                        WHERE b.city_id = %s
                    """,
                        (city_id,),
                    )
                else:
                    cur.execute("SELECT COUNT(*) as count FROM referrals")
                stats["total_referrals"] = cur.fetchone()["count"]

                # Registrations today
                cur.execute(
                    f"""
                    SELECT COUNT(*) as count FROM buildings
                    WHERE DATE(registered_at) = CURRENT_DATE{city_filter}
                """,
                    city_params,
                )
                stats["registrations_today"] = cur.fetchone()["count"]

                return stats
    except Exception as e:
        logger.error(f"[DB] Error getting stats: {e}")
        return {}
