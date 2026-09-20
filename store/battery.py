# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared storage asset (Quartierakku) records and their cost shares.

One asset per LEG community. The asset carries its capacity and annual
cost; the shares carry each participant's percentage of that cost. Storage
failures fail closed with :class:`BillingStoreError`: a missing record is a
normal state (no battery), but a broken persistence layer must never look
like one.
"""

import logging
from decimal import Decimal

from store.billing import BillingStoreError, _get_connection

logger = logging.getLogger(__name__)


def save_battery_asset(community_id: str, asset: dict) -> int:
    """Insert or replace the community's storage asset with its shares.

    Replace is deliberate: the asset record is configuration, not history.
    Billing periods freeze the battery block they were computed with, so a
    corrected configuration never rewrites a persisted draft. A community
    owns at most one asset.
    """
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM billing_storage_assets
                WHERE community_id = %s
                """,
                (community_id,),
            )
            cur.execute(
                """
                INSERT INTO billing_storage_assets
                    (community_id, name, capacity_kwh, annual_cost_chf)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (
                    community_id,
                    asset["name"],
                    asset["capacity_kwh"],
                    asset["annual_cost_chf"],
                ),
            )
            asset_id = cur.fetchone()["id"]
            for participant_id, share_pct in asset["shares"]:
                cur.execute(
                    """
                    INSERT INTO billing_storage_shares
                        (asset_id, participant_id, share_pct)
                    VALUES (%s, %s, %s)
                    """,
                    (asset_id, participant_id, share_pct),
                )
            return asset_id
    except BillingStoreError:
        raise
    except Exception as e:
        logger.error(f"[DB] Error saving battery asset: {e}")
        raise BillingStoreError("Could not save the storage asset") from e


def get_battery_asset(community_id: str) -> dict | None:
    """Return the community's asset with its shares, or None without one."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, community_id, name, capacity_kwh, annual_cost_chf
                FROM billing_storage_assets
                WHERE community_id = %s
                """,
                (community_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            asset = dict(row)
            cur.execute(
                """
                SELECT participant_id, share_pct
                FROM billing_storage_shares
                WHERE asset_id = %s
                ORDER BY participant_id
                """,
                (asset["id"],),
            )
            asset["shares"] = [
                (share["participant_id"], Decimal(str(share["share_pct"])))
                for share in cur.fetchall()
            ]
            return asset
    except BillingStoreError:
        raise
    except Exception as e:
        logger.error(f"[DB] Error loading battery asset: {e}")
        raise BillingStoreError("Could not load the storage asset") from e


def update_battery_asset(community_id: str, asset: dict) -> int:
    """Rewrite the community's asset in place; same fail-closed contract."""
    return save_battery_asset(community_id, asset)
