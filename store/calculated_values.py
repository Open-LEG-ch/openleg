# SPDX-License-Identifier: AGPL-3.0-or-later
"""Persistence adapter for VNB-calculated LEG values and source evidence."""

import hashlib
import json
import re

from store.operator_api import enqueue_event


class CalculatedValuesLifecycleMissing(ValueError):
    """The authenticated tenant has no such accepted delivery."""


class CalculatedValuesLifecycleConflict(ValueError):
    """The command conflicts with an existing immutable event."""


def _get_connection():
    import database

    return database.get_connection()


def get_calculated_values_community(community_id):
    """Resolve ownership through the community administrator's tenant."""
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.community_id, b.city_id AS territory,
                   COALESCE(
                       jsonb_agg(jsonb_build_object(
                           'participant_id', mp.building_id,
                           'direction', direction.value
                       )) FILTER (
                           WHERE mp.building_id IS NOT NULL
                             AND direction.value IS NOT NULL
                       ),
                       '[]'::jsonb
                   ) AS expected_series
            FROM communities c
            JOIN buildings b ON b.building_id = c.admin_building_id
            LEFT JOIN metering_points mp
              ON mp.community_id = c.community_id AND mp.active = TRUE
            LEFT JOIN LATERAL unnest(mp.expected_directions) direction(value)
              ON TRUE
            WHERE c.community_id = %s
            GROUP BY c.community_id, b.city_id
            """,
            (community_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def find_overlapping_calculated_values(
    territory, community_id, period_start, period_end
):
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT content_fingerprint FROM vnb_calculated_values_deliveries d
            WHERE territory = %s AND community_id = %s AND status = 'accepted'
              AND period_start < %s AND period_end > %s
              AND NOT EXISTS (SELECT 1 FROM vnb_calculated_values_events e WHERE e.delivery_id = d.id)
            """,
            (territory, community_id, period_end, period_start),
        )
        return [{"fingerprint": row["content_fingerprint"]} for row in cur.fetchall()]


def save_calculated_values_delivery(delivery):
    """Persist evidence and normalized records atomically; replay is a no-op."""
    with _get_connection() as conn, conn.cursor() as cur:
        if delivery.get("community_id"):
            # Match withdrawal/approval lock order before advisory or FK locks.
            cur.execute(
                "SELECT community_id FROM communities WHERE community_id = %s FOR UPDATE",
                (delivery["community_id"],),
            )
        if delivery["status"] == "accepted":
            lock_key = (
                f"calculated-values:{delivery['territory']}:{delivery['community_id']}"
            )
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (lock_key,),
            )
            cur.execute(
                """
                SELECT content_fingerprint
                FROM vnb_calculated_values_deliveries d
                WHERE territory = %s AND community_id = %s
                  AND status = 'accepted' AND period_start < %s AND period_end > %s
                  AND content_fingerprint <> %s
                  AND NOT EXISTS (SELECT 1 FROM vnb_calculated_values_events e WHERE e.delivery_id = d.id)
                LIMIT 1
                """,
                (
                    delivery["territory"],
                    delivery["community_id"],
                    delivery["period_end"],
                    delivery["period_start"],
                    delivery["fingerprint"],
                ),
            )
            if cur.fetchone():
                delivery = {
                    **delivery,
                    "status": "rejected",
                    "diagnostics": [
                        *delivery["diagnostics"],
                        {"code": "overlapping_period"},
                    ],
                }
        cur.execute(
            """
            INSERT INTO vnb_calculated_values_deliveries
                (contract_version, format_version, transport, territory,
                 community_id, period_start, period_end, timezone, source,
                 vnb_case_id, content_fingerprint, evidence_sha256,
                 evidence_bytes, status, diagnostics, normalized_records,
                 record_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s)
            ON CONFLICT (territory, content_fingerprint) DO NOTHING
            RETURNING *, FALSE AS replayed
            """,
            (
                delivery["contract_version"],
                delivery["format_version"],
                delivery["transport"],
                delivery["territory"],
                delivery["community_id"],
                delivery["period_start"],
                delivery["period_end"],
                delivery["timezone"],
                delivery["source"],
                delivery["vnb_case_id"],
                delivery["fingerprint"],
                delivery["evidence_sha256"],
                delivery["evidence_bytes"],
                delivery["status"],
                json.dumps(delivery["diagnostics"]),
                json.dumps(delivery["normalized_records"]),
                delivery["record_count"],
            ),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                """
                SELECT d.*, TRUE AS replayed,
                    COALESCE((SELECT e.action FROM vnb_calculated_values_events e
                              WHERE e.delivery_id = d.id), 'current') AS lifecycle_state
                FROM vnb_calculated_values_deliveries d
                WHERE territory = %s AND content_fingerprint = %s
                """,
                (delivery["territory"], delivery["fingerprint"]),
            )
            row = cur.fetchone()
        projected = _delivery_row(row)
        if projected.get("id") is not None and delivery.get("community_id"):
            enqueue_event(
                cur,
                "metering.calculated-delivery.received",
                str(projected["id"]),
                delivery["community_id"],
                {
                    "status": projected.get("status"),
                    "period_start": _iso_moment(projected.get("period_start")),
                    "period_end": _iso_moment(projected.get("period_end")),
                },
            )
        return projected


def get_validated_calculated_values(community_id, period_start, period_end):
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM vnb_calculated_values_deliveries d
            WHERE community_id = %s AND period_start = %s AND period_end = %s
              AND status = 'accepted'
              AND NOT EXISTS (SELECT 1 FROM vnb_calculated_values_events e WHERE e.delivery_id = d.id)
            ORDER BY received_at DESC LIMIT 1
            """,
            (community_id, period_start, period_end),
        )
        row = cur.fetchone()
        return _delivery_row(row) if row else None


def list_calculated_values_deliveries(territory, limit=100):
    """Operator projection excludes raw evidence and normalized private rows."""
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, contract_version, format_version, transport, territory,
                   community_id, period_start, period_end, timezone, source,
                   vnb_case_id, content_fingerprint, evidence_sha256, status,
                   diagnostics, record_count, received_at,
                   CASE WHEN status = 'accepted' THEN
                       COALESCE((SELECT e.action FROM vnb_calculated_values_events e
                                 WHERE e.delivery_id = d.id), 'current')
                       ELSE status END AS lifecycle_state
            FROM vnb_calculated_values_deliveries d
            WHERE territory = %s ORDER BY received_at DESC LIMIT %s
            """,
            (territory, limit),
        )
        return [_delivery_row(row) for row in cur.fetchall()]


def _delivery_row(row):
    result = dict(row)
    result["fingerprint"] = result.pop("content_fingerprint", result.get("fingerprint"))
    for field in ("diagnostics", "normalized_records"):
        if isinstance(result.get(field), str):
            result[field] = json.loads(result[field])
    result.pop("evidence_bytes", None)
    if result.get("status") == "accepted":
        result.setdefault("lifecycle_state", "current")
    return result


def withdraw_calculated_values_delivery(
    territory, delivery_id, idempotency_key, reason
):
    """Append an administrator's withdrawal statement; never erase its source."""
    if not isinstance(idempotency_key, str) or not re.fullmatch(
        r"[A-Za-z0-9._-]{1,128}", idempotency_key
    ):
        raise ValueError("invalid_idempotency_key")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise ValueError("invalid_reason")
    reason = reason.strip()
    fingerprint = hashlib.sha256(
        json.dumps(
            {"delivery_id": delivery_id, "territory": territory, "reason": reason},
            sort_keys=True,
        ).encode()
    ).hexdigest()
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT d.* FROM vnb_calculated_values_deliveries d
               JOIN communities c ON c.community_id = d.community_id
               JOIN buildings b ON b.building_id = c.admin_building_id
               WHERE d.id = %s AND d.territory = %s AND b.city_id = %s""",
            (delivery_id, territory, territory),
        )
        delivery = cur.fetchone()
        if not delivery or delivery["status"] != "accepted":
            raise CalculatedValuesLifecycleMissing()
        cur.execute(
            "SELECT community_id FROM communities WHERE community_id = %s FOR UPDATE",
            (delivery["community_id"],),
        )
        cur.execute(
            """SELECT 1 FROM communities c JOIN buildings b
               ON b.building_id = c.admin_building_id
               WHERE c.community_id = %s AND b.city_id = %s FOR SHARE OF b""",
            (delivery["community_id"], territory),
        )
        if not cur.fetchone():
            raise CalculatedValuesLifecycleMissing()
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"calculated-lifecycle:{territory}:{idempotency_key}",),
        )
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"calculated-values:{territory}:{delivery['community_id']}",),
        )
        cur.execute(
            """SELECT * FROM vnb_calculated_values_events
               WHERE territory = %s AND idempotency_key = %s""",
            (territory, idempotency_key),
        )
        previous = cur.fetchone()
        if previous:
            if previous["request_fingerprint"] != fingerprint:
                raise CalculatedValuesLifecycleConflict("idempotency_conflict")
            return {
                "id": previous["id"],
                "action": previous["action"],
                "delivery_id": previous["delivery_id"],
                "replayed": True,
            }
        cur.execute(
            "SELECT id FROM vnb_calculated_values_events WHERE delivery_id = %s",
            (delivery_id,),
        )
        if cur.fetchone():
            raise CalculatedValuesLifecycleConflict("delivery_already_retired")
        cur.execute(
            """INSERT INTO vnb_calculated_values_events
               (territory, community_id, delivery_id, action, idempotency_key,
                request_fingerprint, authenticated_actor, reason)
               VALUES (%s, %s, %s, 'withdrawn', %s, %s, 'admin-token', %s)
               RETURNING id, action, delivery_id""",
            (
                territory,
                delivery["community_id"],
                delivery_id,
                idempotency_key,
                fingerprint,
                reason,
            ),
        )
        event = dict(cur.fetchone())
        cur.execute(
            """UPDATE billing_periods SET status = 'invalidated'
               WHERE community_id = %s AND calculated_values_fingerprint = %s
               AND status = 'draft'""",
            (delivery["community_id"], delivery["content_fingerprint"]),
        )
        enqueue_event(
            cur,
            "metering.calculated-delivery.withdrawn",
            str(event["id"]),
            delivery["community_id"],
            {
                "delivery_id": delivery_id,
                "fingerprint": delivery["content_fingerprint"],
            },
        )
        return {**event, "replayed": False}


def _iso_moment(value):
    """Serialize timestamps so event payloads stay JSON-serializable."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
