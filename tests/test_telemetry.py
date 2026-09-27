# SPDX-License-Identifier: AGPL-3.0-or-later
"""Operational telemetry through authenticated HTTP and real PostgreSQL."""

import json
from datetime import datetime, timedelta, timezone

import pytest

import billing_readings
import database as db
import telemetry
from store import telemetry as store
from tests import test_dashboard_access_routes, test_interest_postgres

app_module = test_dashboard_access_routes.app_module
interest_database = test_interest_postgres.interest_database
pytestmark = pytest.mark.integration
BASE = "/api/telemetry/v1/communities/community-a/installations"


@pytest.fixture
def telemetry_client(interest_database, app_module):
    for actor in ("owner", "viewer", "other"):
        assert db.save_building(
            actor,
            f"{actor}@example.ch",
            {"address": "Synthetic street", "lat": 47, "lon": 8, "bfs_number": 261},
            {"share_with_neighbors": True},
            verified=True,
        )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES ('community-a','A'),('community-b','B')"
        )
        cur.execute(
            "INSERT INTO community_members(community_id,building_id,status,role) VALUES ('community-a','owner','confirmed','member'),('community-a','viewer','confirmed','admin'),('community-b','other','confirmed','admin')"
        )
    client = app_module.web.test_client()
    test_dashboard_access_routes._set_session(client, "owner")
    return client


def enroll(client, **overrides):
    payload = {
        "name": "Roof",
        "device_id": "inverter-1",
        "source_id": "local-reader",
        "cadence_seconds": 30,
        **overrides,
    }
    response = client.post(BASE, json=payload, headers={"X-CSRF-Token": "csrf-secret"})
    assert response.status_code == 201, response.text
    return response.json["installation"]["id"], response.json["credential"]


def sample(**overrides):
    return {
        "sample_id": "sample-1",
        "sequence": 1,
        "device_id": "inverter-1",
        "source_id": "local-reader",
        "cadence_seconds": 30,
        "metric": "power",
        "unit": "W",
        "value": "123.5",
        "direction": "generation",
        "measurement_location": "pv_inverter",
        "quality": "measured",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        **overrides,
    }


def ingest(client, installation, token, samples):
    return client.post(
        f"{BASE}/{installation}/samples",
        json={"schema_version": "telemetry/1", "samples": samples},
        headers={"Authorization": "Bearer " + token},
    )


def test_owner_enrolment_and_private_ingestion(telemetry_client):
    client = telemetry_client
    installation, token = enroll(client)
    assert token.startswith("olt_")
    response = ingest(client, installation, token, [sample()])
    assert response.status_code == 201, response.text
    assert response.json == {
        "schema_version": "telemetry/1",
        "accepted": 1,
        "duplicates": 0,
    }
    response = client.get(f"{BASE}/{installation}/samples")
    assert response.status_code == 200
    row = response.json["samples"][0]
    assert row["value"] == "123.5"
    assert row["observed_at"] and row["received_at"]
    assert row["freshness"] == "fresh"
    assert response.headers["Cache-Control"] == "no-store"
    assert token not in response.text
    with db.get_connection() as conn, conn.cursor() as cur:
        for table in ("meter_readings", "metering_point_readings", "billing_periods"):
            cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
            assert cur.fetchone()["n"] == 0
    now = datetime.now(timezone.utc)
    with pytest.raises(billing_readings.PeriodDataError, match="no_readings"):
        billing_readings.load_period_frames(
            "community-a", now - timedelta(hours=1), now
        )
    assert (
        client.get(
            "/api/operator/v1/communities/community-a/billing/periods",
            headers={"Authorization": "Bearer " + token},
        ).status_code
        == 401
    )


def change(client, installation, action, payload=None):
    return client.post(
        f"{BASE}/{installation}/{action}",
        json=payload,
        headers={"X-CSRF-Token": "csrf-secret"},
    )


def test_credentials_are_bound_and_write_only(telemetry_client):
    client = telemetry_client
    first, token = enroll(client)
    second, second_token = enroll(client)
    assert ingest(client, second, token, [sample()]).status_code == 401
    wrong = client.post(
        f"{BASE.replace('community-a', 'community-b')}/{first}/samples",
        json={"schema_version": "telemetry/1", "samples": [sample()]},
        headers={"Authorization": "Bearer " + token},
    )
    assert wrong.status_code == 404
    with client.session_transaction() as session:
        session.clear()
    for suffix in ("samples", "aggregates", "export"):
        assert (
            client.get(
                f"{BASE}/{first}/{suffix}", headers={"Authorization": "Bearer " + token}
            ).status_code
            == 401
        )
    assert ingest(client, first, token, [sample()]).status_code == 201
    assert ingest(client, second, second_token, [sample()]).status_code == 201


def test_neighbor_consent_and_admin_role_do_not_grant_access(telemetry_client):
    client = telemetry_client
    installation, token = enroll(client)
    assert ingest(client, installation, token, [sample()]).status_code == 201
    test_dashboard_access_routes._set_session(client, "viewer")
    for suffix in ("samples", "aggregates", "export"):
        assert client.get(f"{BASE}/{installation}/{suffix}").status_code == 403
    assert change(client, installation, "rotate").status_code == 403
    test_dashboard_access_routes._set_session(client, "owner")
    assert (
        change(
            client, installation, "share", {"viewer_building_id": "other"}
        ).status_code
        == 403
    )
    assert (
        change(
            client, installation, "share", {"viewer_building_id": "viewer"}
        ).status_code
        == 200
    )
    test_dashboard_access_routes._set_session(client, "viewer")
    for suffix in ("samples", "aggregates", "export"):
        assert client.get(f"{BASE}/{installation}/{suffix}").status_code == 200
    assert (
        change(
            client,
            installation,
            "retention",
            {"raw_retention_days": 1, "aggregate_retention_days": 1},
        ).status_code
        == 403
    )
    test_dashboard_access_routes._set_session(client, "owner")
    assert (
        change(
            client, installation, "unshare", {"viewer_building_id": "viewer"}
        ).status_code
        == 200
    )
    test_dashboard_access_routes._set_session(client, "viewer")
    for suffix in ("samples", "aggregates", "export"):
        assert client.get(f"{BASE}/{installation}/{suffix}").status_code == 403
    test_dashboard_access_routes._set_session(client, "other")
    assert client.get(f"{BASE}/{installation}/samples").status_code == 403


def test_membership_and_verification_are_checked_on_every_use(telemetry_client):
    client = telemetry_client
    installation, token = enroll(client)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE community_members SET status='invited' WHERE building_id='owner'"
        )
    assert ingest(client, installation, token, [sample()]).status_code == 403
    assert client.get(f"{BASE}/{installation}/samples").status_code == 403
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE community_members SET status='confirmed' WHERE building_id='owner'"
        )
        cur.execute("UPDATE buildings SET verified=FALSE WHERE building_id='owner'")
    assert ingest(client, installation, token, [sample()]).status_code == 403


@pytest.mark.parametrize(
    "override",
    [
        {"value": "NaN"},
        {"value": "Infinity"},
        {"value": True},
        {"value": -1},
        {"value": "1e99"},
        {"value": "0.00000000001"},
        {"unit": "kW"},
        {"metric": "command"},
        {"direction": "export"},
        {"direction": []},
        {"measurement_location": "unknown"},
        {"quality": "validated"},
        {"observed_at": "2026-01-01T00:00:00"},
        {
            "observed_at": (
                datetime.now(timezone.utc) + timedelta(minutes=10)
            ).isoformat()
        },
        {"observed_at": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()},
        {"sequence": True},
        {"url": "http://device.local/command"},
        {"interval_start": "2026-01-01T00:00:00Z"},
    ],
)
def test_invalid_samples_are_rejected_atomically(telemetry_client, override):
    client = telemetry_client
    installation, token = enroll(client)
    response = ingest(
        client,
        installation,
        token,
        [
            sample(),
            sample(
                sample_id="second",
                sequence=2,
                **{k: v for k, v in override.items() if k != "sequence"},
            )
            if "sequence" not in override
            else sample(**override),
        ],
    )
    assert response.status_code == 400, response.text
    assert client.get(f"{BASE}/{installation}/samples").json["samples"] == []
    assert client.get(f"{BASE}/{installation}/aggregates").json["aggregates"] == []


@pytest.mark.parametrize(
    "override",
    [{"device_id": "other"}, {"source_id": "other"}, {"cadence_seconds": 60}],
)
def test_source_binding(telemetry_client, override):
    installation, token = enroll(telemetry_client)
    assert (
        ingest(telemetry_client, installation, token, [sample(**override)]).status_code
        == 403
    )


def test_missing_stale_and_skewed_timestamps_never_use_receipt_for_freshness(
    telemetry_client,
):
    client = telemetry_client
    installation, token = enroll(client)
    now = datetime.now(timezone.utc)
    values = [
        sample(observed_at=None),
        sample(
            sample_id="old",
            sequence=2,
            observed_at=(now - timedelta(hours=2)).isoformat(),
        ),
        sample(
            sample_id="future",
            sequence=3,
            observed_at=(now + timedelta(minutes=2)).isoformat(),
        ),
    ]
    assert ingest(client, installation, token, values).status_code == 201
    rows = client.get(f"{BASE}/{installation}/samples").json["samples"]
    assert [r["freshness"] for r in rows] == ["unknown", "stale", "clock_skew"]
    assert rows[0]["observed_at"] is None
    assert (
        sum(
            r["sample_count"]
            for r in client.get(f"{BASE}/{installation}/aggregates").json["aggregates"]
        )
        == 2
    )


def test_counter_interval_and_power_stay_distinct(telemetry_client):
    client = telemetry_client
    installation, token = enroll(client)
    now = datetime.now(timezone.utc)
    values = [
        sample(),
        sample(
            sample_id="counter",
            sequence=2,
            metric="energy_counter",
            unit="Wh",
            value=98765,
        ),
        sample(
            sample_id="interval",
            sequence=3,
            metric="interval_energy",
            unit="Wh",
            value=42,
            observed_at=now.isoformat(),
            interval_start=(now - timedelta(minutes=15)).isoformat(),
            interval_end=now.isoformat(),
        ),
    ]
    assert ingest(client, installation, token, values).status_code == 201
    rows = client.get(f"{BASE}/{installation}/samples").json["samples"]
    assert [(r["metric"], r["unit"]) for r in rows] == [
        ("power", "W"),
        ("energy_counter", "Wh"),
        ("interval_energy", "Wh"),
    ]
    assert len(client.get(f"{BASE}/{installation}/aggregates").json["aggregates"]) == 3


def test_replay_keeps_receipt_and_aggregate_and_survives_cleanup(telemetry_client):
    client = telemetry_client
    installation, token = enroll(client)
    original = sample(observed_at=None)
    assert ingest(client, installation, token, [original]).json["accepted"] == 1
    receipt = client.get(f"{BASE}/{installation}/samples").json["samples"][0][
        "received_at"
    ]
    retry = ingest(client, installation, token, [original])
    assert retry.json == {
        "schema_version": "telemetry/1",
        "accepted": 0,
        "duplicates": 1,
    }
    assert (
        client.get(f"{BASE}/{installation}/samples").json["samples"][0]["received_at"]
        == receipt
    )
    assert (
        ingest(
            client,
            installation,
            token,
            [{**original, "observed_at": datetime.now(timezone.utc).isoformat()}],
        ).status_code
        == 409
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE telemetry_samples SET received_at=CURRENT_TIMESTAMP-INTERVAL '8 days'"
        )
    assert client.get(f"{BASE}/{installation}/samples").json["samples"] == []
    assert store.cleanup()["samples_deleted"] == 1
    assert ingest(client, installation, token, [original]).status_code == 409
    assert (
        ingest(
            client, installation, token, [sample(sample_id="renamed", sequence=1)]
        ).status_code
        == 409
    )
    token = change(client, installation, "rotate").json["credential"]
    assert ingest(client, installation, token, [original]).status_code == 409
    assert client.get(f"{BASE}/{installation}/samples").json["samples"] == []


def test_duplicate_known_samples_do_not_inflate_aggregates(telemetry_client):
    client = telemetry_client
    installation, token = enroll(client)
    original = sample()
    assert ingest(client, installation, token, [original]).status_code == 201
    assert ingest(client, installation, token, [original]).json["duplicates"] == 1
    assert (
        client.get(f"{BASE}/{installation}/aggregates").json["aggregates"][0][
            "sample_count"
        ]
        == 1
    )


def test_revocation_rotation_and_deletion(telemetry_client):
    client = telemetry_client
    installation, token = enroll(client)
    assert ingest(client, installation, token, [sample()]).status_code == 201
    assert change(client, installation, "revoke").status_code == 200
    assert ingest(client, installation, token, [sample(sequence=2)]).status_code == 401
    assert client.get(f"{BASE}/{installation}/samples").status_code == 200
    rotated = change(client, installation, "rotate").json
    assert rotated["highwater"] == 1
    assert ingest(client, installation, token, [sample()]).status_code == 401
    assert (
        ingest(
            client,
            installation,
            rotated["credential"],
            [sample(sequence=2, sample_id="next")],
        ).status_code
        == 201
    )
    assert (
        change(
            client, installation, "share", {"viewer_building_id": "viewer"}
        ).status_code
        == 200
    )
    response = client.delete(
        f"{BASE}/{installation}", headers={"X-CSRF-Token": "csrf-secret"}
    )
    assert response.status_code == 200
    assert (
        ingest(client, installation, rotated["credential"], [sample()]).status_code
        == 404
    )
    assert client.get(f"{BASE}/{installation}/export").status_code == 404
    with db.get_connection() as conn, conn.cursor() as cur:
        for table in (
            "telemetry_installations",
            "telemetry_samples",
            "telemetry_aggregates",
            "telemetry_shares",
        ):
            cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
            assert cur.fetchone()["n"] == 0


def test_shorter_retention_hides_and_deletes_old_data(telemetry_client):
    client = telemetry_client
    installation, token = enroll(client)
    assert (
        ingest(
            client,
            installation,
            token,
            [
                sample(
                    observed_at=(
                        datetime.now(timezone.utc) - timedelta(days=2)
                    ).isoformat()
                )
            ],
        ).status_code
        == 201
    )
    assert (
        change(
            client,
            installation,
            "retention",
            {"raw_retention_days": 1, "aggregate_retention_days": 1},
        ).status_code
        == 200
    )
    assert client.get(f"{BASE}/{installation}/samples").json["samples"] == []
    assert client.get(f"{BASE}/{installation}/aggregates").json["aggregates"] == []
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM telemetry_samples")
        assert cur.fetchone()["n"] == 0
        cur.execute("SELECT COUNT(*) AS n FROM telemetry_aggregates")
        assert cur.fetchone()["n"] == 0
    assert (
        change(
            client,
            installation,
            "retention",
            {"raw_retention_days": 8, "aggregate_retention_days": 90},
        ).status_code
        == 400
    )


def test_rate_limit_counts_invalid_payloads_and_payload_size_is_bounded(
    telemetry_client, monkeypatch
):
    client = telemetry_client
    installation, token = enroll(client)
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    monkeypatch.setattr(telemetry, "RATE_PER_MINUTE", 2)
    response = client.post(
        f"{BASE}/{installation}/samples",
        data="x" * (telemetry.MAX_BYTES + 1),
        headers=headers,
    )
    assert response.status_code == 413
    assert (
        client.post(
            f"{BASE}/{installation}/samples", data="{bad", headers=headers
        ).status_code
        == 400
    )
    response = ingest(client, installation, token, [sample()])
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert response.headers["Cache-Control"] == "no-store"


def test_management_requires_csrf_and_credentials_are_hashed(telemetry_client, caplog):
    client = telemetry_client
    installation, token = enroll(client)
    assert client.post(f"{BASE}/{installation}/rotate").status_code == 403
    assert client.delete(f"{BASE}/{installation}").status_code == 403
    assert (
        ingest(client, installation, token, [sample(value="987654321.123")]).status_code
        == 201
    )
    assert (
        ingest(
            client,
            installation,
            token,
            [sample(value="987654321.123", device_id="private-source")],
        ).status_code
        == 403
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM telemetry_installations")
        assert token not in json.dumps(dict(cur.fetchone()), default=str)
    assert token not in caplog.text
    assert "987654321.123" not in caplog.text
    assert "private-source" not in caplog.text


def test_revocation_between_rate_check_and_write_is_respected(
    telemetry_client, monkeypatch
):
    client = telemetry_client
    installation, token = enroll(client)
    charge = store.charge_ingestion

    def revoke_after_charge(community, installation, credential):
        charge(community, installation, credential)
        store.change(community, installation, "owner", "revoke")

    monkeypatch.setattr(store, "charge_ingestion", revoke_after_charge)
    assert ingest(client, installation, token, [sample()]).status_code == 401
    assert client.get(f"{BASE}/{installation}/samples").json["samples"] == []


def test_aggregate_expiry_is_enforced_before_cleanup(telemetry_client):
    client = telemetry_client
    installation, token = enroll(client)
    assert ingest(client, installation, token, [sample()]).status_code == 201
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE telemetry_aggregates SET hour=CURRENT_TIMESTAMP-INTERVAL '91 days'"
        )
    assert client.get(f"{BASE}/{installation}/aggregates").json["aggregates"] == []
    assert store.cleanup()["aggregates_deleted"] == 1


def test_profile_deletion_cascades_operational_data(telemetry_client):
    client = telemetry_client
    installation, token = enroll(client)
    assert ingest(client, installation, token, [sample()]).status_code == 201
    assert (
        change(
            client, installation, "share", {"viewer_building_id": "viewer"}
        ).status_code
        == 200
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM buildings WHERE building_id='owner'")
        for table in (
            "telemetry_installations",
            "telemetry_samples",
            "telemetry_aggregates",
            "telemetry_shares",
        ):
            cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
            assert cur.fetchone()["n"] == 0
