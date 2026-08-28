# SPDX-License-Identifier: AGPL-3.0-or-later
"""LEG community billing repository.

Repository module for the LEG community billing domain: billing periods,
billing line items, and communities. The connection seam is resolved via
``database.get_connection`` at call time so existing tests that
``monkeypatch.setattr(database, "get_connection", ...)`` keep working
unchanged and ``database`` can re-export these functions for legacy callers.
"""

import json
import logging

logger = logging.getLogger(__name__)


class BillingStoreError(RuntimeError):
    """Billing data could not be loaded from persistent storage."""


class BillingPolicyConflict(BillingStoreError):
    """A billing policy version already exists for this effective date."""


def _is_unique_violation(error) -> bool:
    return getattr(error, "pgcode", None) == "23505"


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
                     total_surplus_kwh, total_network_discount_chf, distribution_model,
                     network_level, internal_price_chf_per_kwh, grid_fee_chf_per_kwh,
                     timezone, input_fingerprint, source_document_ids,
                     reconciliation, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s::jsonb, %s::jsonb, 'draft')
                    RETURNING id
                """,
                    (
                        community_id,
                        period_start,
                        period_end,
                        summary["total_production_kwh"],
                        summary["total_allocated_kwh"],
                        summary.get("total_surplus_kwh", 0),
                        summary["total_network_discount_chf"],
                        summary.get("distribution_model", "proportional"),
                        summary.get("network_level", "same"),
                        summary.get("internal_price_chf_per_kwh"),
                        summary.get("grid_fee_chf_per_kwh"),
                        summary.get("timezone", "Europe/Zurich"),
                        summary.get("input_fingerprint"),
                        json.dumps(summary.get("source_document_ids", [])),
                        json.dumps(summary.get("reconciliation", {})),
                    ),
                )
                period_id = cur.fetchone()["id"]

                line_items = summary.get("line_items", [])
                participants = {
                    participant["id"]: participant
                    for participant in summary.get("participants", [])
                }
                for item in line_items:
                    participant = (
                        participants.get(item["participant_id"], {})
                        if item["item_type"] == "consumer_charge"
                        else {}
                    )
                    cur.execute(
                        """
                        INSERT INTO billing_line_items
                        (billing_period_id, participant_id, item_type, quantity_kwh,
                         unit_price_chf_per_kwh, amount_chf, consumption_kwh,
                         allocated_kwh, self_supply_ratio, internal_cost_chf,
                         network_discount_chf)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                        (
                            period_id,
                            item["participant_id"],
                            item["item_type"],
                            item["quantity_kwh"],
                            item["unit_price_chf_per_kwh"],
                            item["amount_chf"],
                            participant.get("consumption_kwh"),
                            participant.get("allocated_kwh"),
                            participant.get("self_supply_ratio"),
                            participant.get("internal_cost_chf"),
                            participant.get("network_discount_chf"),
                        ),
                    )

                if not line_items:
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
        raise


def get_active_communities() -> list[dict]:
    """Get all communities with status='active'."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM communities WHERE status = 'active'")
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error getting active communities: {e}")
        raise BillingStoreError("Could not load active communities") from e


def get_community_for_building(building_id: str) -> dict | None:
    """Get community for a building via community_members join."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
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


def list_billing_periods(limit: int = 100) -> list[dict]:
    """List persisted billing periods, newest period first."""
    bounded_limit = max(1, min(int(limit), 500))
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM billing_periods
                ORDER BY period_start DESC, id DESC
                LIMIT %s
                """,
                (bounded_limit,),
            )
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error listing billing periods: {e}")
        raise BillingStoreError("Could not list billing periods") from e


def get_billing_period(period_id: int, community_id: str | None = None) -> dict | None:
    """Get billing period with line items."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            if community_id is None:
                cur.execute("SELECT * FROM billing_periods WHERE id = %s", (period_id,))
            else:
                cur.execute(
                    """
                    SELECT * FROM billing_periods
                    WHERE id = %s AND community_id = %s
                    """,
                    (period_id, community_id),
                )
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
        raise BillingStoreError("Could not load billing period") from e


def get_billing_period_for_window(
    community_id: str, period_start, period_end
) -> dict | None:
    """Return the immutable period occupying a community window, if any."""
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, input_fingerprint FROM billing_periods
            WHERE community_id = %s AND period_start = %s AND period_end = %s
            """,
            (community_id, period_start, period_end),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_billing_policy(community_id: str, period_start, period_end) -> dict | None:
    """Return the complete effective policy covering the billing period.

    Fails closed: the newest version with ``effective_from <= period_start`` is
    selected first. Coverage, completeness and interior-boundary checks are
    applied only to that exact row, so an expired or incomplete newest version
    can never fall back to an older complete version.
    """
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH newest AS (
                SELECT t.id, t.community_id, t.internal_price_chf_per_kwh,
                       t.grid_fee_chf_per_kwh, t.network_level,
                       t.distribution_model, t.vat_mode, t.vat_rate_pct,
                       t.payment_days, t.invoice_prefix, t.delivery_method,
                       t.effective_from, t.effective_to
                FROM billing_tariffs t
                JOIN communities c ON c.community_id = t.community_id
                WHERE t.community_id = %s
                  AND c.status = 'active'
                  AND t.effective_from <= %s
                ORDER BY t.effective_from DESC
                LIMIT 1
            )
            SELECT id AS tariff_id, internal_price_chf_per_kwh,
                   grid_fee_chf_per_kwh, network_level,
                   distribution_model, vat_mode, vat_rate_pct,
                   payment_days, invoice_prefix, delivery_method,
                   effective_from
            FROM newest t
            WHERE (t.effective_to IS NULL OR t.effective_to >= %s)
              AND t.distribution_model IS NOT NULL
              AND t.vat_mode IS NOT NULL
              AND t.vat_rate_pct IS NOT NULL
              AND t.payment_days IS NOT NULL
              AND t.invoice_prefix IS NOT NULL
              AND t.delivery_method IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM billing_tariffs newer
                  WHERE newer.community_id = t.community_id
                    AND newer.effective_from > %s
                    AND newer.effective_from < %s
              )
            """,
            (community_id, period_start, period_end, period_start, period_end),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def save_billing_policy(community_id: str, policy: dict) -> int:
    """Insert one immutable billing policy version.

    Insert-only: a new effective version never mutates earlier versions. A
    duplicate (community_id, effective_from) is refused with
    BillingPolicyConflict; any other storage failure fails closed with
    BillingStoreError.
    """
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO billing_tariffs
                (community_id, effective_from, internal_price_chf_per_kwh,
                 grid_fee_chf_per_kwh, network_level, distribution_model,
                 vat_mode, vat_rate_pct, payment_days, invoice_prefix,
                 delivery_method)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    community_id,
                    policy["effective_from"],
                    policy["internal_price_chf_per_kwh"],
                    policy["grid_fee_chf_per_kwh"],
                    policy["network_level"],
                    policy["distribution_model"],
                    policy["vat_mode"],
                    policy["vat_rate_pct"],
                    policy["payment_days"],
                    policy["invoice_prefix"],
                    policy["delivery_method"],
                ),
            )
            return cur.fetchone()["id"]
    except Exception as e:
        if _is_unique_violation(e):
            raise BillingPolicyConflict(
                "A billing policy version already exists for this effective date"
            ) from e
        logger.error(f"[DB] Error saving billing policy: {e}")
        raise BillingStoreError("Could not save billing policy") from e


def list_billing_policies(community_id: str) -> list[dict]:
    """List all policy versions of one community, newest effective date first."""
    try:
        with _get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM billing_tariffs
                WHERE community_id = %s
                ORDER BY effective_from DESC, id DESC
                """,
                (community_id,),
            )
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Error listing billing policies: {e}")
        raise BillingStoreError("Could not list billing policies") from e
