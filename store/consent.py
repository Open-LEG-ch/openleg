# SPDX-License-Identifier: AGPL-3.0-or-later
"""Data consent repository.

Owns the consent record a resident gives and can revoke. The neighbour gate
reads the same table through store.building; this module owns the writes.
"""

import logging

logger = logging.getLogger(__name__)


def _get_connection():
    import database

    return database.get_connection()


def save_data_consent(
    building_id,
    tier=1,
    share_municipality=True,
    share_research=False,
    share_providers=False,
    version="1.0",
):
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO data_consents (building_id, tier, share_with_municipality, share_anonymized_research,
                        share_aggregated_providers, consent_version)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (building_id) DO UPDATE SET
                        tier = EXCLUDED.tier,
                        share_with_municipality = EXCLUDED.share_with_municipality,
                        share_anonymized_research = EXCLUDED.share_anonymized_research,
                        share_aggregated_providers = EXCLUDED.share_aggregated_providers,
                        consent_version = EXCLUDED.consent_version,
                        consented_at = CURRENT_TIMESTAMP, revoked_at = NULL
                """,
                    (
                        building_id,
                        tier,
                        share_municipality,
                        share_research,
                        share_providers,
                        version,
                    ),
                )
                return True
    except Exception as e:
        logger.error(f"[DB] Error saving data consent: {e}")
        return False


def get_data_consent(building_id):
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM data_consents WHERE building_id = %s AND revoked_at IS NULL",
                    (building_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Error getting data consent: {e}")
        return None


def count_consented_buildings(tier=None):
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                if tier:
                    cur.execute(
                        "SELECT COUNT(*) as count FROM data_consents WHERE tier >= %s AND revoked_at IS NULL",
                        (tier,),
                    )
                else:
                    cur.execute(
                        "SELECT COUNT(*) as count FROM data_consents WHERE revoked_at IS NULL"
                    )
                return cur.fetchone()["count"]
    except Exception as e:
        logger.error(f"[DB] Error counting consented buildings: {e}")
        return 0
