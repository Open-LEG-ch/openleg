# SPDX-License-Identifier: AGPL-3.0-or-later
"""Derived owner notices with bounded state and explicit acknowledgement episodes."""

import uuid
from datetime import timedelta
from decimal import Decimal

from psycopg2.extras import Json

from telemetry import TelemetryError, integer

COOLDOWN = timedelta(hours=1)


def create_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS telemetry_notices (
            id UUID PRIMARY KEY,
            installation_id UUID NOT NULL REFERENCES telemetry_installations ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK (kind IN ('collector_late','source_missing','source_old',
                'source_channel_missing','source_unknown','source_clock','device_fault')),
            channel_key TEXT NOT NULL,
            episode BIGINT NOT NULL DEFAULT 1 CHECK (episode > 0),
            active BOOLEAN NOT NULL DEFAULT TRUE,
            raised_at TIMESTAMPTZ NOT NULL,
            acknowledged_at TIMESTAMPTZ,
            snoozed_until TIMESTAMPTZ,
            resolved_at TIMESTAMPTZ,
            recovery TEXT CHECK (recovery IN ('readings_current','condition_changed','device_report_clear')),
            UNIQUE (installation_id,kind,channel_key)
        );
        CREATE INDEX IF NOT EXISTS telemetry_notice_expiry ON telemetry_notices (resolved_at)
            WHERE active=FALSE;
    """)
    cur.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_constraint
                WHERE conrelid='telemetry_notices'::regclass AND conname='telemetry_notices_kind_check'
                AND position('device_fault' in pg_get_constraintdef(oid))=0) THEN
                ALTER TABLE telemetry_notices DROP CONSTRAINT telemetry_notices_kind_check;
                ALTER TABLE telemetry_notices ADD CONSTRAINT telemetry_notices_kind_check
                    CHECK (kind IN ('collector_late','source_missing','source_old',
                        'source_channel_missing','source_unknown','source_clock','device_fault'));
            END IF;
            IF EXISTS (SELECT 1 FROM pg_constraint
                WHERE conrelid='telemetry_notices'::regclass AND conname='telemetry_notices_recovery_check'
                AND position('device_report_clear' in pg_get_constraintdef(oid))=0) THEN
                ALTER TABLE telemetry_notices DROP CONSTRAINT telemetry_notices_recovery_check;
                ALTER TABLE telemetry_notices ADD CONSTRAINT telemetry_notices_recovery_check
                    CHECK (recovery IN ('readings_current','condition_changed','device_report_clear'));
            END IF;
        END $$;
    """)


def _conditions(installation):
    if installation["delivery_status"] == "missing":
        return [("source_missing", "delivery")]
    if installation["delivery_status"] == "late":
        return [("collector_late", "delivery")]
    result = []
    for sample in installation["samples"]:
        freshness = sample["freshness"]
        if freshness == "fresh":
            if sample["metric"] == "device_fault" and Decimal(sample["value"]) == 1:
                result.append(("device_fault", "device_fault:device:status"))
            continue
        if freshness == "stale":
            kind = (
                "source_old"
                if sample["delivery_status"] == "recent"
                else "source_channel_missing"
            )
        elif freshness == "clock_skew":
            kind = "source_clock"
        else:
            kind = "source_unknown"
        channel = ":".join(
            sample[key] for key in ("metric", "measurement_location", "direction")
        )
        result.append((kind, channel))
    return result


def _public(row, now):
    result = {
        key: value.isoformat() if hasattr(value, "isoformat") else value
        for key, value in row.items()
    }
    result["id"] = str(row["id"])
    result["installation_id"] = str(row["installation_id"])
    result["attention_required"] = row["active"] and (
        row["snoozed_until"] is None or row["snoozed_until"] <= now
    )
    return result


def reconcile(cur, installations, now, *, uncertain=None):
    """Caller holds installation locks; keep one row per condition/channel."""
    if not installations:
        return
    ids = [installation["id"] for installation in installations]
    if uncertain is None:
        uncertain = [
            installation["id"]
            for installation in installations
            if installation.get("_notice_refresh_pending")
        ]
    if uncertain:
        # A missed projection can hide an intermediate recovery. Require a new
        # confirmation instead of reusing acknowledgement from that uncertain span.
        cur.execute(
            """
            UPDATE telemetry_notices SET episode=episode+1,raised_at=%s,
                acknowledged_at=NULL,snoozed_until=NULL
            WHERE installation_id=ANY(%s::uuid[]) AND active=TRUE
            """,
            (now, uncertain),
        )
    desired = [
        {
            "id": str(uuid.uuid4()),
            "installation_id": installation["id"],
            "kind": kind,
            "channel_key": channel,
        }
        for installation in installations
        for kind, channel in _conditions(installation)
    ]
    healthy = [
        {
            "installation_id": installation["id"],
            "channel_key": ":".join(
                sample[key] for key in ("metric", "measurement_location", "direction")
            ),
        }
        for installation in installations
        for sample in installation["samples"]
        if sample["freshness"] == "fresh" and sample["delivery_status"] == "recent"
    ]
    healthy.extend(
        {"installation_id": installation["id"], "channel_key": "delivery"}
        for installation in installations
        if installation["samples"]
        and all(
            sample["freshness"] == "fresh" and sample["delivery_status"] == "recent"
            for sample in installation["samples"]
        )
    )
    if desired:
        cur.execute(
            """
            INSERT INTO telemetry_notices (id,installation_id,kind,channel_key,raised_at)
            SELECT d.id,d.installation_id,d.kind,d.channel_key,%s
            FROM jsonb_to_recordset(%s::jsonb) AS d(id UUID,installation_id UUID,kind TEXT,channel_key TEXT)
            ON CONFLICT (installation_id,kind,channel_key) DO UPDATE SET
                active=TRUE,
                episode=CASE WHEN telemetry_notices.active THEN telemetry_notices.episode
                    ELSE telemetry_notices.episode+1 END,
                raised_at=CASE WHEN telemetry_notices.active THEN telemetry_notices.raised_at ELSE EXCLUDED.raised_at END,
                acknowledged_at=CASE WHEN telemetry_notices.active THEN telemetry_notices.acknowledged_at END,
                snoozed_until=CASE WHEN telemetry_notices.active THEN telemetry_notices.snoozed_until END,
                resolved_at=NULL,recovery=NULL
            """,
            (now, Json(desired)),
        )
    cur.execute(
        """
        WITH desired AS (
            SELECT * FROM jsonb_to_recordset(%s::jsonb)
                AS d(installation_id UUID,kind TEXT,channel_key TEXT)
        ), healthy AS (
            SELECT * FROM jsonb_to_recordset(%s::jsonb)
                AS h(installation_id UUID,channel_key TEXT)
        )
        UPDATE telemetry_notices n SET active=FALSE,resolved_at=%s,
            recovery=CASE WHEN EXISTS (SELECT 1 FROM healthy h WHERE h.installation_id=n.installation_id
                AND h.channel_key=n.channel_key)
                THEN CASE WHEN n.kind='device_fault' THEN 'device_report_clear' ELSE 'readings_current' END
                ELSE 'condition_changed' END
        FROM telemetry_installations i
        WHERE i.id=n.installation_id AND n.installation_id=ANY(%s::uuid[])
            AND (n.active=TRUE OR (
                n.kind='device_fault' AND n.recovery='condition_changed'
                AND n.resolved_at >= %s-i.raw_retention_days*INTERVAL '1 day'
                AND EXISTS (SELECT 1 FROM healthy h WHERE h.installation_id=n.installation_id
                    AND h.channel_key=n.channel_key)
            ))
            AND NOT EXISTS (SELECT 1 FROM desired d WHERE d.installation_id=n.installation_id
                AND d.kind=n.kind AND d.channel_key=n.channel_key)
        """,
        (Json(desired), Json(healthy), now, ids, now),
    )
    cur.execute(
        """
        SELECT n.* FROM telemetry_notices n JOIN telemetry_installations i ON i.id=n.installation_id
        WHERE n.installation_id=ANY(%s::uuid[]) AND
            (n.active=TRUE OR n.resolved_at >= %s-i.raw_retention_days*INTERVAL '1 day')
        ORDER BY n.raised_at,n.id
        """,
        (ids, now),
    )
    by_id = {installation["id"]: installation for installation in installations}
    for installation in installations:
        installation["notices"] = []
    for row in cur.fetchall():
        by_id[str(row["installation_id"])]["notices"].append(_public(row, now))
    cur.execute(
        "UPDATE telemetry_installations SET notice_refresh_pending=FALSE WHERE id=ANY(%s::uuid[])",
        (ids,),
    )
    for installation in installations:
        installation.pop("_notice_refresh_pending", None)


def acknowledge(cur, installation, notice_id, episode, now):
    integer(episode, 1, 2**63 - 1)
    cur.execute(
        "SELECT * FROM telemetry_notices WHERE id=%s AND installation_id=%s",
        (notice_id, installation),
    )
    row = cur.fetchone()
    if row is None:
        raise TelemetryError("notice_not_found", 404)
    if row["episode"] != episode or not row["active"]:
        raise TelemetryError("notice_changed", 409)
    if row["snoozed_until"] is None or row["snoozed_until"] <= now:
        cur.execute(
            "UPDATE telemetry_notices SET acknowledged_at=%s,snoozed_until=%s WHERE id=%s RETURNING *",
            (now, now + COOLDOWN, notice_id),
        )
        row = cur.fetchone()
    return _public(row, now)


def cleanup(cur, installation=None):
    cur.execute(
        """
        DELETE FROM telemetry_notices n USING telemetry_installations i
        WHERE i.id=n.installation_id AND (%s::uuid IS NULL OR i.id=%s::uuid)
            AND n.active=FALSE AND n.resolved_at < CURRENT_TIMESTAMP-i.raw_retention_days*INTERVAL '1 day'
        """,
        (installation, installation),
    )
