# SPDX-License-Identifier: AGPL-3.0-or-later
"""Quartierakku repository: one shared battery asset per community.

Repository module for the shared battery domain. The connection seam is
resolved via ``database.get_connection`` at call time so tests can
monkeypatch it unchanged. Invalid configs are refused before any write;
storage outages fail closed with :class:`BillingStoreError`.
"""

import quartierakku

__all__ = ["BillingStoreError", "InvalidBatteryConfig", "get_battery", "save_battery"]

from store.billing import BillingStoreError

InvalidBatteryConfig = quartierakku.InvalidBatteryConfig


def _get_connection():
    import database

    return database.get_connection()


def save_battery(community_id: str, config: dict) -> None:
    """Create or replace one community battery with its per-member shares.

    The exact record is upserted and the shares rewritten in one
    transaction. An invalid config raises
    :class:`quartierakku.InvalidBatteryConfig` before touching storage.
    """
    try:
        normalized = quartierakku.validate_battery_config(
            {**config, "community_id": community_id}
        )
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO community_batteries (community_id, capacity_kwh, annual_cost_chf)
                VALUES (%s, %s, %s)
                ON CONFLICT (community_id) DO UPDATE
                SET capacity_kwh = EXCLUDED.capacity_kwh,
                    annual_cost_chf = EXCLUDED.annual_cost_chf,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    community_id,
                    normalized["capacity_kwh"],
                    normalized["annual_cost_chf"],
                ),
            )
            cur.execute(
                "DELETE FROM community_battery_shares WHERE community_id = %s",
                (community_id,),
            )
            for participant, share in normalized["shares"].items():
                cur.execute(
                    """
                    INSERT INTO community_battery_shares
                    (community_id, building_id, share_pct)
                    VALUES (%s, %s, %s)
                    """,
                    (community_id, participant, share),
                )
    except quartierakku.InvalidBatteryConfig:
        raise
    except Exception as e:
        raise BillingStoreError("Could not save the battery record") from e


def get_battery(community_id: str) -> dict | None:
    """Read one community's battery asset with its cost shares, or None."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT community_id, capacity_kwh, annual_cost_chf
                FROM community_batteries
                WHERE community_id = %s
                """,
                (community_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            config = dict(row)
            cur.execute(
                """
                SELECT building_id, share_pct
                FROM community_battery_shares
                WHERE community_id = %s
                ORDER BY building_id
                """,
                (community_id,),
            )
            config["shares"] = {
                share["building_id"]: share["share_pct"] for share in cur.fetchall()
            }
            return config
    except Exception as e:
        raise BillingStoreError("Could not load the battery record") from e
