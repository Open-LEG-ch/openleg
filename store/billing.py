# SPDX-License-Identifier: AGPL-3.0-or-later
"""LEG community billing repository.

Repository module for the LEG community billing domain: billing periods,
billing line items, and communities. The connection seam is resolved via
``database.get_connection`` at call time so existing tests that
``monkeypatch.setattr(database, "get_connection", ...)`` keep working
unchanged and ``database`` can re-export these functions for legacy callers.
"""

import logging
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


def _get_connection():
    import database

    return database.get_connection()


# === Billing Operations ===


def save_billing_period(
    community_id: str, period_start, period_end, summary: dict
) -> int:
    """Save billing period and line items from billing engine output."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO billing_periods
                    (community_id, period_start, period_end, total_production_kwh, total_allocated_kwh,
                     total_surplus_kwh, total_network_discount_chf, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'final') RETURNING id
                """,
                    (
                        community_id,
                        period_start,
                        period_end,
                        summary["total_production_kwh"],
                        summary["total_allocated_kwh"],
                        summary.get("total_surplus_kwh", 0),
                        summary["total_network_discount_chf"],
                    ),
                )
                period_id = cur.fetchone()[0]

                for p in summary.get("participants", []):
                    cur.execute(
                        """
                        INSERT INTO billing_line_items
                        (billing_period_id, participant_id, consumption_kwh, allocated_kwh,
                         self_supply_ratio, internal_cost_chf, network_discount_chf)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                        (
                            period_id,
                            p["id"],
                            p["consumption_kwh"],
                            p["allocated_kwh"],
                            p["self_supply_ratio"],
                            p["internal_cost_chf"],
                            p["network_discount_chf"],
                        ),
                    )

                return period_id
    except Exception as e:
        logger.error(f"[DB] Error saving billing period: {e}")
        return 0


def get_active_communities() -> List[Dict]:
    """Get all communities with status='active'."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM communities WHERE status = 'active'")
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting active communities: {e}")
        return []


def get_community_for_building(building_id: str) -> Optional[Dict]:
    """Get community for a building via community_members join."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.* FROM communities c
                    JOIN community_members cm ON c.community_id = cm.community_id
                    WHERE cm.building_id = %s AND c.status = 'active'
                    LIMIT 1
                """,
                    (building_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting community for building: {e}")
        return None


def get_billing_period(period_id: int) -> Optional[Dict]:
    """Get billing period with line items."""
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM billing_periods WHERE id = %s", (period_id,))
                period = cur.fetchone()
                if not period:
                    return None
                result = dict(period)
                cur.execute(
                    "SELECT * FROM billing_line_items WHERE billing_period_id = %s",
                    (period_id,),
                )
                result["line_items"] = [dict(row) for row in cur.fetchall()]
                return result
    except Exception as e:
        logger.error(f"[DB] Error getting billing period: {e}")
        return None
