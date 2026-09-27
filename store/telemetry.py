# SPDX-License-Identifier: AGPL-3.0-or-later
"""Private operational storage, isolated from validated meters and billing."""

import hashlib
import hmac
import secrets
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from psycopg2.extras import Json

import telemetry as contract
from telemetry import TelemetryError


@contextmanager
def _cursor():
    import database

    try:
        with database.get_connection() as conn, conn.cursor() as cur:
            yield cur
    except TelemetryError:
        raise
    except Exception:
        # Do not propagate SQL parameters, samples or credential material.
        raise TelemetryError("telemetry_unavailable", 503) from None


def create_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS telemetry_installations (
            id UUID PRIMARY KEY,
            community_id VARCHAR(64) NOT NULL,
            owner_building_id VARCHAR(64) NOT NULL,
            name TEXT NOT NULL,
            device_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            cadence_seconds INTEGER NOT NULL CHECK (cadence_seconds BETWEEN 1 AND 86400),
            raw_retention_days INTEGER NOT NULL DEFAULT 7 CHECK (raw_retention_days BETWEEN 1 AND 7),
            aggregate_retention_days INTEGER NOT NULL DEFAULT 90 CHECK (aggregate_retention_days BETWEEN 1 AND 90),
            credential_hash TEXT,
            highwater BIGINT NOT NULL DEFAULT 0,
            rate_window TIMESTAMPTZ,
            rate_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (community_id, owner_building_id)
                REFERENCES community_members (community_id, building_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS telemetry_owner ON telemetry_installations (owner_building_id);
        CREATE TABLE IF NOT EXISTS telemetry_samples (
            installation_id UUID NOT NULL REFERENCES telemetry_installations ON DELETE CASCADE,
            sample_id TEXT NOT NULL,
            sequence BIGINT NOT NULL,
            fingerprint TEXT NOT NULL,
            observation JSONB NOT NULL,
            observed_at TIMESTAMPTZ,
            received_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (installation_id, sample_id),
            UNIQUE (installation_id, sequence)
        );
        CREATE INDEX IF NOT EXISTS telemetry_observed ON telemetry_samples (installation_id, observed_at);
        CREATE TABLE IF NOT EXISTS telemetry_aggregates (
            installation_id UUID NOT NULL REFERENCES telemetry_installations ON DELETE CASCADE,
            hour TIMESTAMPTZ NOT NULL,
            dimensions JSONB NOT NULL,
            sample_count BIGINT NOT NULL,
            minimum NUMERIC NOT NULL,
            maximum NUMERIC NOT NULL,
            total NUMERIC NOT NULL,
            PRIMARY KEY (installation_id, hour, dimensions)
        );
        CREATE TABLE IF NOT EXISTS telemetry_shares (
            installation_id UUID NOT NULL REFERENCES telemetry_installations ON DELETE CASCADE,
            viewer_building_id VARCHAR(64) NOT NULL REFERENCES buildings ON DELETE CASCADE,
            PRIMARY KEY (installation_id, viewer_building_id)
        );
    """)


def _member(cur, community, actor):
    cur.execute(
        """
        SELECT 1 FROM community_members m JOIN buildings b USING (building_id)
        WHERE m.community_id=%s AND m.building_id=%s
            AND m.status='confirmed' AND b.verified=TRUE
    """,
        (community, actor),
    )
    if cur.fetchone() is None:
        raise TelemetryError("telemetry_forbidden", 403)


def _installation(cur, community, installation):
    cur.execute(
        "SELECT * FROM telemetry_installations WHERE id=%s AND community_id=%s FOR UPDATE",
        (installation, community),
    )
    row = cur.fetchone()
    if row is None:
        raise TelemetryError("telemetry_not_found", 404)
    _member(cur, community, row["owner_building_id"])
    return row


def _access(cur, community, installation, actor, *, owner=False):
    _member(cur, community, actor)
    row = _installation(cur, community, installation)
    if row["owner_building_id"] != actor:
        cur.execute(
            "SELECT 1 FROM telemetry_shares WHERE installation_id=%s AND viewer_building_id=%s",
            (installation, actor),
        )
        if owner or cur.fetchone() is None:
            raise TelemetryError("telemetry_forbidden", 403)
    return row


def _credential():
    token = "olt_" + secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


def _public(row):
    return {
        key: (str(value) if key == "id" else value)
        for key, value in row.items()
        if key
        not in {"credential_hash", "rate_window", "rate_count", "owner_building_id"}
    }


def enroll(community, actor, payload):
    fields = contract.enrollment(payload)
    token, digest = _credential()
    with _cursor() as cur:
        _member(cur, community, actor)
        # Serialize per-owner enrollment so concurrent requests cannot exceed the cap.
        cur.execute("SELECT 1 FROM buildings WHERE building_id=%s FOR UPDATE", (actor,))
        cur.execute(
            "SELECT COUNT(*) AS n FROM telemetry_installations WHERE owner_building_id=%s",
            (actor,),
        )
        if cur.fetchone()["n"] >= 20:
            raise TelemetryError("installation_limit", 409)
        cur.execute(
            """
            INSERT INTO telemetry_installations
                (id,community_id,owner_building_id,name,device_id,source_id,cadence_seconds,
                 raw_retention_days,aggregate_retention_days,credential_hash)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
        """,
            (
                str(uuid.uuid4()),
                community,
                actor,
                fields["name"],
                fields["device_id"],
                fields["source_id"],
                fields["cadence_seconds"],
                fields["raw_retention_days"],
                fields["aggregate_retention_days"],
                digest,
            ),
        )
        return {"installation": _public(cur.fetchone()), "credential": token}


def list_installations(community, actor):
    with _cursor() as cur:
        _member(cur, community, actor)
        cur.execute(
            "SELECT * FROM telemetry_installations WHERE community_id=%s AND owner_building_id=%s ORDER BY created_at",
            (community, actor),
        )
        return [_public(row) for row in cur.fetchall()]


def _authenticate(row, token):
    if (
        not isinstance(token, str)
        or not token.startswith("olt_")
        or not 40 <= len(token) <= 64
        or not row["credential_hash"]
        or not hmac.compare_digest(
            row["credential_hash"], hashlib.sha256(token.encode()).hexdigest()
        )
    ):
        raise TelemetryError("invalid_telemetry_credential", 401)


def charge_ingestion(community, installation, token):
    """Commit rate accounting even if subsequent payload validation fails."""
    with _cursor() as cur:
        row = _installation(cur, community, installation)
        _authenticate(row, token)
        cur.execute(
            """
            UPDATE telemetry_installations SET
                rate_count=CASE WHEN rate_window=date_trunc('minute',CURRENT_TIMESTAMP)
                    THEN rate_count+1 ELSE 1 END,
                rate_window=date_trunc('minute',CURRENT_TIMESTAMP)
            WHERE id=%s RETURNING rate_count
        """,
            (installation,),
        )
        count = cur.fetchone()["rate_count"]
    if count > contract.RATE_PER_MINUTE:
        raise TelemetryError("telemetry_rate_limit", 429)


def ingest(community, installation, token, payload):
    with _cursor() as cur:
        row = _installation(cur, community, installation)
        # Recheck under the write lock: revocation may race with payload parsing.
        _authenticate(row, token)
        now = datetime.now(timezone.utc)
        samples = contract.observations(payload, row, now)
        accepted = duplicates = 0
        highwater = row["highwater"]
        for sample in samples:
            cur.execute(
                "SELECT fingerprint FROM telemetry_samples WHERE installation_id=%s AND sample_id=%s",
                (installation, sample["sample_id"]),
            )
            existing = cur.fetchone()
            if existing:
                if existing["fingerprint"] != sample["fingerprint"]:
                    raise TelemetryError("sample_conflict", 409)
                duplicates += 1
                continue
            if sample["sequence"] <= highwater:
                raise TelemetryError("sequence_replay", 409)
            highwater = sample["sequence"]
            cur.execute(
                """
                INSERT INTO telemetry_samples (installation_id,sample_id,sequence,fingerprint,observation,observed_at)
                VALUES (%s,%s,%s,%s,%s,%s)
            """,
                (
                    installation,
                    sample["sample_id"],
                    sample["sequence"],
                    sample["fingerprint"],
                    Json({k: v for k, v in sample.items() if k != "fingerprint"}),
                    sample["observed_at"],
                ),
            )
            # Hourly statistics only. Never integrate W or difference Wh counters.
            if sample["observed_at"] is not None:
                hour = contract.timestamp(sample["observed_at"]).replace(
                    minute=0, second=0, microsecond=0
                )
                dimensions = {
                    key: sample[key]
                    for key in (
                        "metric",
                        "unit",
                        "direction",
                        "measurement_location",
                        "quality",
                        "device_id",
                        "source_id",
                        "cadence_seconds",
                    )
                }
                cur.execute(
                    """
                    INSERT INTO telemetry_aggregates
                        (installation_id,hour,dimensions,sample_count,minimum,maximum,total)
                    VALUES (%s,%s,%s,1,%s,%s,%s)
                    ON CONFLICT (installation_id,hour,dimensions) DO UPDATE SET
                        sample_count=telemetry_aggregates.sample_count+1,
                        minimum=LEAST(telemetry_aggregates.minimum,EXCLUDED.minimum),
                        maximum=GREATEST(telemetry_aggregates.maximum,EXCLUDED.maximum),
                        total=telemetry_aggregates.total+EXCLUDED.total
                """,
                    (
                        installation,
                        hour,
                        Json(dimensions),
                        sample["value"],
                        sample["value"],
                        sample["value"],
                    ),
                )
            accepted += 1
        cur.execute(
            "UPDATE telemetry_installations SET highwater=%s WHERE id=%s",
            (highwater, installation),
        )
        return {
            "schema_version": contract.VERSION,
            "accepted": accepted,
            "duplicates": duplicates,
        }


def read(community, installation, actor, *, aggregates=False, after=0):
    with _cursor() as cur:
        _access(cur, community, installation, actor)
        if aggregates:
            cur.execute(
                """
                SELECT a.* FROM telemetry_aggregates a JOIN telemetry_installations i ON i.id=a.installation_id
                WHERE i.id=%s AND hour >= CURRENT_TIMESTAMP-i.aggregate_retention_days*INTERVAL '1 day'
                ORDER BY hour DESC, dimensions LIMIT 1000
            """,
                (installation,),
            )
            return {
                "schema_version": contract.VERSION,
                "aggregates": [
                    {
                        "hour": r["hour"].isoformat(),
                        **r["dimensions"],
                        "sample_count": r["sample_count"],
                        "minimum": str(r["minimum"]),
                        "maximum": str(r["maximum"]),
                        "mean": str(r["total"] / r["sample_count"]),
                    }
                    for r in cur.fetchall()
                ],
            }
        cur.execute(
            """
            SELECT s.* FROM telemetry_samples s JOIN telemetry_installations i ON i.id=s.installation_id
            WHERE i.id=%s AND s.sequence>%s
                AND received_at >= CURRENT_TIMESTAMP-i.raw_retention_days*INTERVAL '1 day'
                AND (observed_at IS NULL OR observed_at >= CURRENT_TIMESTAMP-i.raw_retention_days*INTERVAL '1 day')
            ORDER BY sequence LIMIT 1000
        """,
            (installation, after),
        )
        now = datetime.now(timezone.utc)
        samples = [
            {
                **r["observation"],
                "received_at": r["received_at"].isoformat(),
                "freshness": contract.freshness(r["observation"], now),
            }
            for r in cur.fetchall()
        ]
        return {
            "schema_version": contract.VERSION,
            "samples": samples,
            "next_after": samples[-1]["sequence"] if samples else after,
        }


def change(community, installation, actor, action, payload=None):
    with _cursor() as cur:
        _access(cur, community, installation, actor, owner=True)
        if action == "delete":
            cur.execute(
                "DELETE FROM telemetry_installations WHERE id=%s", (installation,)
            )
        elif action == "revoke":
            cur.execute(
                "UPDATE telemetry_installations SET credential_hash=NULL WHERE id=%s",
                (installation,),
            )
        elif action == "rotate":
            token, digest = _credential()
            cur.execute(
                "UPDATE telemetry_installations SET credential_hash=%s WHERE id=%s RETURNING highwater",
                (digest, installation),
            )
            return {"credential": token, "highwater": cur.fetchone()["highwater"]}
        elif action == "retention":
            if not isinstance(payload, dict) or set(payload) != {
                "raw_retention_days",
                "aggregate_retention_days",
            }:
                raise TelemetryError()
            policy = contract.retention(payload)
            cur.execute(
                "UPDATE telemetry_installations SET raw_retention_days=%s,aggregate_retention_days=%s WHERE id=%s",
                (
                    policy["raw_retention_days"],
                    policy["aggregate_retention_days"],
                    installation,
                ),
            )
            _cleanup(cur, installation)
        elif action in ("share", "unshare"):
            if not isinstance(payload, dict) or set(payload) != {"viewer_building_id"}:
                raise TelemetryError()
            viewer = contract.identifier(payload["viewer_building_id"])
            if action == "share":
                _member(cur, community, viewer)
                cur.execute(
                    "INSERT INTO telemetry_shares VALUES (%s,%s) ON CONFLICT DO NOTHING",
                    (installation, viewer),
                )
            else:
                cur.execute(
                    "DELETE FROM telemetry_shares WHERE installation_id=%s AND viewer_building_id=%s",
                    (installation, viewer),
                )
        else:
            raise TelemetryError("telemetry_not_found", 404)
    return {"ok": True}


def _cleanup(cur, installation=None):
    cur.execute(
        """
        DELETE FROM telemetry_samples s USING telemetry_installations i
        WHERE i.id=s.installation_id AND (%s::uuid IS NULL OR i.id=%s::uuid)
            AND (received_at < CURRENT_TIMESTAMP-i.raw_retention_days*INTERVAL '1 day'
                 OR observed_at < CURRENT_TIMESTAMP-i.raw_retention_days*INTERVAL '1 day')
    """,
        (installation, installation),
    )
    raw = cur.rowcount
    cur.execute(
        """
        DELETE FROM telemetry_aggregates a USING telemetry_installations i
        WHERE i.id=a.installation_id AND (%s::uuid IS NULL OR i.id=%s::uuid)
            AND hour < CURRENT_TIMESTAMP-i.aggregate_retention_days*INTERVAL '1 day'
    """,
        (installation, installation),
    )
    return {"samples_deleted": raw, "aggregates_deleted": cur.rowcount}


def cleanup():
    with _cursor() as cur:
        return _cleanup(cur)
