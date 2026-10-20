# SPDX-License-Identifier: AGPL-3.0-or-later
"""Private operational storage, isolated from validated meters and billing."""

import hashlib
import hmac
import logging
import secrets
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

from psycopg2.extras import Json

import telemetry as contract
import telemetry_summary
from store import telemetry_notices as notices
from telemetry import TelemetryError

logger = logging.getLogger(__name__)


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
            notice_refresh_pending BOOLEAN NOT NULL DEFAULT FALSE,
            rate_window TIMESTAMPTZ,
            rate_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (community_id, owner_building_id)
                REFERENCES community_members (community_id, building_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS telemetry_owner ON telemetry_installations (owner_building_id);
        ALTER TABLE telemetry_installations
            ADD COLUMN IF NOT EXISTS notice_refresh_pending BOOLEAN NOT NULL DEFAULT FALSE;
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
        CREATE INDEX IF NOT EXISTS telemetry_channel_observed ON telemetry_samples (
            installation_id, (observation->>'metric'),
            (observation->>'measurement_location'), (observation->>'direction'),
            (COALESCE(observed_at, received_at)) DESC, sequence DESC
        );
        CREATE INDEX IF NOT EXISTS telemetry_channel_received ON telemetry_samples (
            installation_id, (observation->>'metric'),
            (observation->>'measurement_location'), (observation->>'direction'),
            received_at DESC
        ) INCLUDE (observed_at);
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
            community_id VARCHAR(64) NOT NULL,
            viewer_building_id VARCHAR(64) NOT NULL,
            PRIMARY KEY (installation_id, viewer_building_id),
            FOREIGN KEY (community_id, viewer_building_id)
                REFERENCES community_members (community_id, building_id) ON DELETE CASCADE
        );
        CREATE OR REPLACE FUNCTION revoke_inactive_telemetry_shares() RETURNS trigger AS $$
        BEGIN
            IF NEW.status IS DISTINCT FROM 'confirmed' THEN
                DELETE FROM telemetry_shares
                    WHERE community_id=NEW.community_id AND viewer_building_id=NEW.building_id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        DROP TRIGGER IF EXISTS telemetry_membership_revoked ON community_members;
        CREATE TRIGGER telemetry_membership_revoked AFTER UPDATE OF status ON community_members
            FOR EACH ROW EXECUTE FUNCTION revoke_inactive_telemetry_shares();
    """)
    notices.create_tables(cur)


def _member(cur, community, actor, *, lock=False):
    cur.execute(
        """
        SELECT 1 FROM community_members m JOIN buildings b USING (building_id)
        WHERE m.community_id=%s AND m.building_id=%s
            AND m.status='confirmed' AND b.verified=TRUE
    """
        + (" FOR SHARE OF m" if lock else ""),
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
        not in {
            "credential_hash",
            "rate_window",
            "rate_count",
            "owner_building_id",
            "notice_refresh_pending",
        }
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


def _limit_overview_query(cur):
    # Bound the page query even under contention or pathological buffering.
    cur.execute("""
        SELECT set_config('statement_timeout',
            LEAST(2000, COALESCE(NULLIF(setting,'0')::int,2000))::text, TRUE)
        FROM pg_settings WHERE name='statement_timeout'
    """)


def owner_overview(actor):
    now = datetime.now(UTC)
    with _cursor() as cur:
        _limit_overview_query(cur)
        cur.execute(
            """
            SELECT i.id FROM telemetry_installations i
            JOIN community_members m ON m.community_id=i.community_id AND m.building_id=i.owner_building_id
            JOIN buildings b ON b.building_id=i.owner_building_id
            WHERE i.owner_building_id=%s AND m.status='confirmed' AND b.verified=TRUE
            ORDER BY i.id FOR UPDATE OF i
            """,
            (actor,),
        )
        locked_ids = [str(row["id"]) for row in cur.fetchall()]
        installations = _owner_overview(cur, actor, now, locked_ids)
        notices.reconcile(cur, installations, now)
        return installations


def shared_overview(community, actor):
    now = datetime.now(UTC)
    with _cursor() as cur:
        _limit_overview_query(cur)
        _member(cur, community, actor, lock=True)
        cur.execute(
            """
            SELECT i.id FROM telemetry_installations i
            JOIN community_members m ON m.community_id=i.community_id AND m.building_id=i.owner_building_id
            JOIN buildings b ON b.building_id=i.owner_building_id AND b.verified=TRUE
            WHERE i.community_id=%s AND m.status='confirmed' AND (
                i.owner_building_id=%s OR EXISTS (
                    SELECT 1 FROM telemetry_shares s
                    JOIN consents c ON c.building_id=i.owner_building_id AND c.share_with_neighbors=TRUE
                    WHERE s.installation_id=i.id AND s.community_id=i.community_id AND s.viewer_building_id=%s
                )
            )
            ORDER BY i.id LIMIT 201 FOR SHARE OF i
            """,
            (community, actor, actor),
        )
        ids = [str(row["id"]) for row in cur.fetchall()]
        if len(ids) > 200:
            raise TelemetryError("telemetry_unavailable", 503)
        installations = _owner_overview(cur, actor, now, ids, include_shared=True)
        return telemetry_summary.view(installations, now, community)


def _owner_overview(cur, actor, now, installation_ids, *, include_shared=False):
    """Seek latest retained channels, rechecking permission in this snapshot."""
    channels = [
        {"metric": metric, "location": location, "direction": direction}
        for metric in contract.UNITS
        for location, directions in contract.LOCATIONS.items()
        for direction in sorted(directions)
        if (metric == "state_of_charge") == (direction == "stored")
        and (metric == "device_fault") == (location == "device")
    ]
    cur.execute(
        """
        WITH request_time AS (SELECT %s::timestamptz AS now)
        SELECT i.*, latest.observation, latest.received_at, latest.recently_received
        FROM telemetry_installations i
        JOIN community_members m ON m.community_id=i.community_id
            AND m.building_id=i.owner_building_id AND m.status='confirmed'
        JOIN buildings b ON b.building_id=i.owner_building_id AND b.verified=TRUE
        CROSS JOIN request_time
        LEFT JOIN LATERAL (
            SELECT observed.observation, observed.received_at,
                COALESCE((
                    SELECT TRUE FROM telemetry_samples recent
                    WHERE recent.installation_id=i.id
                        AND recent.observation->>'metric'=channel.metric
                        AND recent.observation->>'measurement_location'=channel.location
                        AND recent.observation->>'direction'=channel.direction
                        AND COALESCE(recent.observed_at,recent.received_at)
                            >= request_time.now-i.raw_retention_days*INTERVAL '1 day'
                        AND recent.received_at >= GREATEST(
                            request_time.now-i.raw_retention_days*INTERVAL '1 day',
                            request_time.now-GREATEST(%s,i.cadence_seconds*%s)*INTERVAL '1 second'
                        )
                    ORDER BY recent.received_at DESC
                    LIMIT 1
                ), FALSE) AS recently_received
            FROM jsonb_to_recordset(%s::jsonb)
                AS channel(metric TEXT, location TEXT, direction TEXT)
            JOIN LATERAL (
                SELECT s.observation, s.received_at
                FROM telemetry_samples s
                WHERE s.installation_id=i.id
                    AND s.observation->>'metric'=channel.metric
                    AND s.observation->>'measurement_location'=channel.location
                    AND s.observation->>'direction'=channel.direction
                    AND COALESCE(s.observed_at,s.received_at)
                        >= request_time.now-i.raw_retention_days*INTERVAL '1 day'
                    AND s.received_at >= request_time.now-i.raw_retention_days*INTERVAL '1 day'
                ORDER BY COALESCE(s.observed_at,s.received_at) DESC, s.sequence DESC
                LIMIT 1
            ) observed ON TRUE
        ) latest ON TRUE
        WHERE i.id=ANY(%s::uuid[]) AND (
            i.owner_building_id=%s OR (%s AND EXISTS (
                SELECT 1 FROM telemetry_shares s
                JOIN consents c ON c.building_id=i.owner_building_id AND c.share_with_neighbors=TRUE
                JOIN community_members viewer ON viewer.community_id=s.community_id
                    AND viewer.building_id=s.viewer_building_id AND viewer.status='confirmed'
                JOIN buildings vb ON vb.building_id=viewer.building_id AND vb.verified=TRUE
                WHERE s.installation_id=i.id AND s.community_id=i.community_id AND s.viewer_building_id=%s
            ))
        )
        ORDER BY i.created_at, i.id, latest.observation->>'metric',
            latest.observation->>'measurement_location', latest.observation->>'direction'
        """,
        (
            now,
            contract.FRESHNESS_MIN_SECONDS,
            contract.FRESHNESS_CADENCES,
            Json(channels),
            installation_ids,
            actor,
            include_shared,
            actor,
        ),
    )
    installations = {}
    for row in cur.fetchall():
        installation = installations.setdefault(
            str(row["id"]),
            {
                "id": str(row["id"]),
                "community_id": row["community_id"],
                "owner_building_id": row["owner_building_id"],
                "_notice_refresh_pending": row["notice_refresh_pending"],
                "name": row["name"],
                "source_id": row["source_id"],
                "device_id": row["device_id"],
                "cadence_seconds": row["cadence_seconds"],
                "delivery_status": "missing",
                "samples": [],
            },
        )
        if row["observation"] is not None:
            delivery_status = "recent" if row["recently_received"] else "late"
            if installation["delivery_status"] != "recent":
                installation["delivery_status"] = delivery_status
            observed = row["observation"]["observed_at"]
            age = (
                (now - contract.timestamp(observed)).total_seconds()
                if observed
                else None
            )
            installation["samples"].append(
                {
                    **row["observation"],
                    "received_at": row["received_at"].isoformat(),
                    "freshness": contract.freshness(row["observation"], now),
                    "delivery_status": delivery_status,
                    "age_seconds": int(age) if age is not None and age >= 0 else None,
                }
            )
    return list(installations.values())


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
        now = datetime.now(UTC)
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
            if sample["observed_at"] is not None and sample["metric"] != "device_fault":
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
            "UPDATE telemetry_installations SET highwater=%s,notice_refresh_pending=notice_refresh_pending OR %s WHERE id=%s",
            (highwater, accepted > 0, installation),
        )
        cur.execute("SAVEPOINT notice_projection")
        try:
            _limit_overview_query(cur)
            notices.reconcile(
                cur,
                _owner_overview(cur, row["owner_building_id"], now, [installation]),
                now,
                uncertain=[installation] if row["notice_refresh_pending"] else [],
            )
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT notice_projection")
            logger.debug("Telemetry notice reconciliation deferred")
        finally:
            cur.execute("RELEASE SAVEPOINT notice_projection")
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
        now = datetime.now(UTC)
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
                _member(cur, community, viewer, lock=True)
                cur.execute(
                    "INSERT INTO telemetry_shares (installation_id,community_id,viewer_building_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                    (installation, community, viewer),
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
    aggregates = cur.rowcount
    notices.cleanup(cur, installation)
    return {"samples_deleted": raw, "aggregates_deleted": aggregates}


def cleanup():
    with _cursor() as cur:
        return _cleanup(cur)


def _notice_snapshot(cur, community, installation, actor, now):
    _limit_overview_query(cur)
    _access(cur, community, installation, actor, owner=True)
    installations = _owner_overview(cur, actor, now, [installation])
    if not installations:
        raise TelemetryError("telemetry_forbidden", 403)
    notices.reconcile(cur, installations, now)
    return installations[0]["notices"]


def owner_notices(community, installation, actor):
    with _cursor() as cur:
        return {
            "notices": _notice_snapshot(
                cur, community, installation, actor, datetime.now(UTC)
            )
        }


def acknowledge_notice(community, installation, actor, notice_id, payload):
    if not isinstance(payload, dict) or set(payload) != {"episode"}:
        raise TelemetryError()
    now = datetime.now(UTC)
    with _cursor() as cur:
        _notice_snapshot(cur, community, installation, actor, now)
        return notices.acknowledge(
            cur, installation, notice_id, payload["episode"], now
        )
