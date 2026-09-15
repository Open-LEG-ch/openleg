# SPDX-License-Identifier: AGPL-3.0-or-later
"""Persistence adapter for VNB-calculated LEG values and source evidence."""

import json


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
            SELECT content_fingerprint FROM vnb_calculated_values_deliveries
            WHERE territory = %s AND community_id = %s AND status = 'accepted'
              AND period_start < %s AND period_end > %s
            """,
            (territory, community_id, period_end, period_start),
        )
        return [{"fingerprint": row["content_fingerprint"]} for row in cur.fetchall()]


def save_calculated_values_delivery(delivery):
    """Persist evidence and normalized records atomically; replay is a no-op."""
    with _get_connection() as conn, conn.cursor() as cur:
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
                FROM vnb_calculated_values_deliveries
                WHERE territory = %s AND community_id = %s
                  AND status = 'accepted' AND period_start < %s AND period_end > %s
                  AND content_fingerprint <> %s
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
                SELECT *, TRUE AS replayed
                FROM vnb_calculated_values_deliveries
                WHERE territory = %s AND content_fingerprint = %s
                """,
                (delivery["territory"], delivery["fingerprint"]),
            )
            row = cur.fetchone()
        return _delivery_row(row)


def get_validated_calculated_values(community_id, period_start, period_end):
    with _get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM vnb_calculated_values_deliveries
            WHERE community_id = %s AND period_start = %s AND period_end = %s
              AND status = 'accepted'
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
                   diagnostics, record_count, received_at
            FROM vnb_calculated_values_deliveries
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
    return result
