# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cluster repository.

Owns provisional cluster assignments and cluster metadata.
"""

import logging

logger = logging.getLogger(__name__)


def _get_connection():
    import database

    return database.get_connection()


def save_cluster(building_id: str, cluster_id: int) -> bool:
    """Save cluster assignment for a building."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO clusters (building_id, cluster_id)
                    VALUES (%s, %s)
                    ON CONFLICT (building_id) DO UPDATE SET
                        cluster_id = EXCLUDED.cluster_id,
                        updated_at = CURRENT_TIMESTAMP
                """,
                (building_id, cluster_id),
            )
            return True
    except Exception as e:
        logger.error(f"[DB] Error saving cluster: {e}")
        return False


def save_cluster_info(cluster_id: int, info: dict) -> bool:
    """Save cluster metadata."""
    try:
        import json

        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO cluster_info (cluster_id, autarky_percent, num_members, polygon)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (cluster_id) DO UPDATE SET
                        autarky_percent = EXCLUDED.autarky_percent,
                        num_members = EXCLUDED.num_members,
                        polygon = EXCLUDED.polygon,
                        updated_at = CURRENT_TIMESTAMP
                """,
                    (
                        cluster_id,
                        info.get("autarky_percent"),
                        info.get("num_members"),
                        json.dumps(info.get("polygon", [])),
                    ),
                )
                return True
    except Exception as e:
        logger.error(f"[DB] Error saving cluster info: {e}")
        return False
