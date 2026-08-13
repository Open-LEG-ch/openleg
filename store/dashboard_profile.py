# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dashboard profile and consent self-service store.

Resolves the connection seam via ``database.get_connection`` at call time,
so existing test monkeypatches on ``database.get_connection`` keep working.
"""

import logging

logger = logging.getLogger(__name__)


def _get_connection():
    import database

    return database.get_connection()


def update_dashboard_profile(
    building_id: str,
    *,
    annual_consumption_kwh: float | None,
    potential_pv_kwp: float | None,
    share_with_utility: bool,
    share_with_neighbors: bool,
) -> bool:
    """Update building profile and consents atomically.

    Returns True when the building exists and both queries executed.
    Returns False when the building does not exist so no orphan consent
    row is created.
    """
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE buildings
                SET annual_consumption_kwh = %s,
                    potential_pv_kwp = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE building_id = %s
                RETURNING building_id
                """,
                (annual_consumption_kwh, potential_pv_kwp, building_id),
            )
            row = cur.fetchone()
            if row is None:
                return False

            cur.execute(
                """
                INSERT INTO consents (
                    building_id, share_with_neighbors, share_with_utility,
                    consent_timestamp
                ) VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (building_id) DO UPDATE SET
                    share_with_neighbors = EXCLUDED.share_with_neighbors,
                    share_with_utility = EXCLUDED.share_with_utility,
                    consent_timestamp = EXCLUDED.consent_timestamp
                """,
                (building_id, share_with_neighbors, share_with_utility),
            )
            return True
    except Exception:
        logger.exception("Failed to update dashboard profile")
        return False
