# SPDX-License-Identifier: AGPL-3.0-or-later
"""Private notice episodes through HTTP, ingestion and actual PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest

import database as db
from store import telemetry as store
from tests import test_dashboard_access_routes as sessions
from tests import test_telemetry as api

app_module = api.app_module
interest_database = api.interest_database
telemetry_client = api.telemetry_client
pytestmark = pytest.mark.integration


@pytest.fixture
def clock(monkeypatch):
    current = [datetime.now(UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0].astimezone(tz)

    monkeypatch.setattr(store, "datetime", Clock)
    return current


def notices(client, installation):
    response = client.get(f"{api.BASE}/{installation}/notices")
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    return response.json["notices"]


def acknowledge(client, installation, notice, **overrides):
    return client.post(
        f"{api.BASE}/{installation}/notices/{notice['id']}/acknowledge",
        json={"episode": notice["episode"], **overrides},
        headers={"X-CSRF-Token": "csrf-secret"},
    )


def deliver(client, installation, token, clock, *, sequence, age_seconds=0):
    # Fix the database arrival clock in this disposable schema before ingestion.
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "ALTER TABLE telemetry_samples ALTER COLUMN received_at SET DEFAULT %s",
            (clock[0],),
        )
    message = api.sample(
        sample_id=f"reading-{sequence}",
        sequence=sequence,
        value="0",
        observed_at=(clock[0] - timedelta(seconds=age_seconds)).isoformat(),
    )
    assert api.ingest(client, installation, token, [message]).status_code == 201
    return message


def test_acknowledgement_cooldown_recovery_and_new_episode(telemetry_client, clock):
    client = telemetry_client
    installation, token = api.enroll(client)
    deliver(client, installation, token, clock, sequence=1, age_seconds=600)
    first = notices(client, installation)[0]
    assert first["kind"] == "source_old"
    assert first["active"] and first["attention_required"]
    assert first["episode"] == 1
    response = acknowledge(client, installation, first)
    assert response.status_code == 200
    acknowledged = notices(client, installation)[0]
    assert acknowledged["acknowledged_at"] is not None
    assert not acknowledged["attention_required"]
    clock[0] += timedelta(seconds=10)
    assert acknowledge(client, installation, first).status_code == 200
    retried = notices(client, installation)[0]
    assert retried["acknowledged_at"] == acknowledged["acknowledged_at"]
    assert retried["snoozed_until"] == acknowledged["snoozed_until"]
    deliver(client, installation, token, clock, sequence=2, age_seconds=610)
    repeated = notices(client, installation)[0]
    assert repeated["episode"] == 1
    assert repeated["raised_at"] == first["raised_at"]
    assert not repeated["attention_required"]
    clock[0] += timedelta(hours=1)
    deliver(client, installation, token, clock, sequence=3, age_seconds=4210)
    assert notices(client, installation)[0]["attention_required"]
    deliver(client, installation, token, clock, sequence=4)
    recovered = notices(client, installation)[0]
    assert not recovered["active"]
    assert recovered["recovery"] == "readings_current"
    assert recovered["resolved_at"] is not None
    clock[0] += timedelta(seconds=1)
    deliver(client, installation, token, clock, sequence=5, age_seconds=600)
    buffered = notices(client, installation)[0]
    assert not buffered["active"]
    assert buffered["episode"] == 1
    clock[0] += timedelta(seconds=600)
    deliver(client, installation, token, clock, sequence=6, age_seconds=600)
    reopened = notices(client, installation)[0]
    assert reopened["id"] == first["id"]
    assert reopened["episode"] == 2
    assert reopened["acknowledged_at"] is None
    assert reopened["attention_required"]
    assert acknowledge(client, installation, first).status_code == 409


def test_fresh_night_zero_and_estimated_values_do_not_create_faults(
    telemetry_client, clock
):
    client = telemetry_client
    installation, token = api.enroll(client)
    deliver(client, installation, token, clock, sequence=1)
    assert notices(client, installation) == []
    message = api.sample(
        sample_id="estimated",
        sequence=2,
        value="0",
        quality="estimated",
        observed_at=clock[0].isoformat(),
    )
    assert api.ingest(client, installation, token, [message]).status_code == 201
    assert notices(client, installation) == []


def test_missing_delivery_is_distinct_from_unknown_measurement_age(telemetry_client):
    client = telemetry_client
    installation, token = api.enroll(client)
    missing = notices(client, installation)[0]
    assert missing["kind"] == "source_missing"
    assert (
        api.ingest(
            client, installation, token, [api.sample(observed_at=None)]
        ).status_code
        == 201
    )
    current = notices(client, installation)
    assert {row["kind"] for row in current if row["active"]} == {"source_unknown"}
    changed = next(row for row in current if row["kind"] == "source_missing")
    assert changed["recovery"] == "condition_changed"


def test_late_delivery_uses_declared_cadence_and_never_claims_a_device_fault(
    telemetry_client, clock
):
    client = telemetry_client
    installation, token = api.enroll(client, cadence_seconds=300)
    message = api.sample(cadence_seconds=300, observed_at=clock[0].isoformat())
    assert api.ingest(client, installation, token, [message]).status_code == 201
    clock[0] += timedelta(seconds=100)
    assert notices(client, installation) == []
    clock[0] += timedelta(seconds=501)
    current = notices(client, installation)
    assert {row["kind"] for row in current if row["active"]} == {"collector_late"}
    html = client.get("/dashboard/telemetry").text
    assert "Keine neue Kollektor-Lieferung" in html
    assert "Stromausfall" not in html
    assert "Cyberangriff" not in html


@pytest.mark.parametrize("actor", ["viewer", "other"])
def test_shared_viewers_and_admins_cannot_read_or_acknowledge_notices(
    telemetry_client, actor
):
    client = telemetry_client
    installation, _ = api.enroll(client)
    first = notices(client, installation)[0]
    assert (
        api.change(
            client, installation, "share", {"viewer_building_id": "viewer"}
        ).status_code
        == 200
    )
    sessions._set_session(client, actor)
    assert client.get(f"{api.BASE}/{installation}/notices").status_code in {403, 404}
    assert acknowledge(client, installation, first).status_code in {403, 404}


def test_acknowledgement_requires_session_and_csrf(telemetry_client):
    client = telemetry_client
    installation, token = api.enroll(client)
    first = notices(client, installation)[0]
    path = f"{api.BASE}/{installation}/notices/{first['id']}/acknowledge"
    assert client.post(path, json={"episode": 1}).status_code == 403
    assert acknowledge(client, installation, first, episode=True).status_code == 400
    with client.session_transaction() as session:
        session.clear()
    assert (
        client.get(
            f"{api.BASE}/{installation}/notices",
            headers={"Authorization": "Bearer " + token},
        ).status_code
        == 401
    )
    assert (
        client.post(
            path, json={"episode": 1}, headers={"Authorization": "Bearer " + token}
        ).status_code
        == 401
    )


def test_installation_deletion_removes_notices(telemetry_client):
    client = telemetry_client
    installation, _ = api.enroll(client)
    notices(client, installation)
    assert (
        client.delete(
            f"{api.BASE}/{installation}", headers={"X-CSRF-Token": "csrf-secret"}
        ).status_code
        == 200
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM telemetry_notices WHERE installation_id=%s",
            (installation,),
        )
        assert cur.fetchone()["n"] == 0


def test_dashboard_acknowledgement_is_private_and_uses_the_saved_language(
    telemetry_client,
):
    client = telemetry_client
    installation, _ = api.enroll(client)
    first = notices(client, installation)[0]
    path = f"/dashboard/telemetry/{installation}/notices/{first['id']}/acknowledge"
    page = client.get("/dashboard/telemetry?lang=fr-CH")
    assert page.status_code == 200
    assert path in page.text
    assert "Confirmer le message" in page.text
    assert 'name="csrf_token" value="csrf-secret"' in page.text
    assert (
        client.post(
            path, data={"community_id": "community-a", "episode": first["episode"]}
        ).status_code
        == 400
    )
    response = client.post(
        path,
        data={
            "community_id": "community-a",
            "episode": first["episode"],
            "csrf_token": "csrf-secret",
        },
    )
    assert response.status_code == 303
    page = client.get(response.headers["Location"])
    assert page.headers["Cache-Control"] == "no-store"
    assert "Message confirmé" in page.text
    assert "Confirmer le message" not in page.text


def test_expired_channel_is_changed_not_claimed_to_have_recovered(telemetry_client):
    client = telemetry_client
    installation, token = api.enroll(client)
    now = datetime.now(UTC)
    assert (
        api.ingest(
            client,
            installation,
            token,
            [
                api.sample(observed_at=(now - timedelta(minutes=10)).isoformat()),
                api.sample(
                    sample_id="grid",
                    sequence=2,
                    measurement_location="grid_connection",
                    direction="import",
                    observed_at=now.isoformat(),
                ),
            ],
        ).status_code
        == 201
    )
    first = notices(client, installation)[0]
    old = now - timedelta(days=8)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE telemetry_samples SET received_at=%s, observed_at=%s, observation=jsonb_set(observation,'{observed_at}',to_jsonb(%s::text)) WHERE installation_id=%s AND sample_id='sample-1'",
            (old, old, old.isoformat(), installation),
        )
    changed = next(
        row for row in notices(client, installation) if row["id"] == first["id"]
    )
    assert not changed["active"]
    assert changed["recovery"] == "condition_changed"


def test_resolved_notices_expire_at_the_raw_horizon_and_are_physically_cleaned(
    telemetry_client,
):
    client = telemetry_client
    installation, token = api.enroll(client)
    first = notices(client, installation)[0]
    assert api.ingest(client, installation, token, [api.sample()]).status_code == 201
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE telemetry_notices SET resolved_at=%s WHERE id=%s",
            (datetime.now(UTC) - timedelta(days=8), first["id"]),
        )
    assert notices(client, installation) == []
    store.cleanup()
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM telemetry_notices WHERE id=%s", (first["id"],)
        )
        assert cur.fetchone()["n"] == 0


@pytest.mark.parametrize("revocation", ["membership", "verification"])
def test_revoked_owner_cannot_acknowledge_a_saved_notice(telemetry_client, revocation):
    client = telemetry_client
    installation, _ = api.enroll(client)
    first = notices(client, installation)[0]
    with db.get_connection() as conn, conn.cursor() as cur:
        if revocation == "membership":
            cur.execute(
                "UPDATE community_members SET status='invited' WHERE building_id='owner'"
            )
        else:
            cur.execute("UPDATE buildings SET verified=FALSE WHERE building_id='owner'")
    assert acknowledge(client, installation, first).status_code == 403


def test_concurrent_acknowledgements_preserve_one_cooldown(telemetry_client, clock):
    client = telemetry_client
    installation, _ = api.enroll(client)
    first = notices(client, installation)[0]
    barrier = Barrier(2)

    def acknowledge_once():
        barrier.wait(timeout=5)
        return store.acknowledge_notice(
            "community-a",
            installation,
            "owner",
            first["id"],
            {"episode": first["episode"]},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        left, right = pool.submit(acknowledge_once), pool.submit(acknowledge_once)
        first_result, second_result = left.result(timeout=10), right.result(timeout=10)
    assert first_result == second_result
    assert first_result["episode"] == 1
    assert first_result["snoozed_until"] == (clock[0] + timedelta(hours=1)).isoformat()


def test_ingestion_records_recovery_before_a_stale_acknowledgement(
    telemetry_client, clock
):
    client = telemetry_client
    installation, token = api.enroll(client)
    deliver(client, installation, token, clock, sequence=1, age_seconds=600)
    first = notices(client, installation)[0]
    deliver(client, installation, token, clock, sequence=2)
    clock[0] += timedelta(seconds=601)
    deliver(client, installation, token, clock, sequence=3, age_seconds=600)
    # No overview request between the fresh reading and the new incident.
    assert acknowledge(client, installation, first).status_code == 409
    assert notices(client, installation)[0]["episode"] == 2


def test_overview_reconciles_only_installations_it_locked(
    telemetry_client, monkeypatch
):
    from threading import Event, current_thread

    client = telemetry_client
    first, _ = api.enroll(client)
    before_read, allow_read = Event(), Event()
    before_reconcile, allow_reconcile = Event(), Event()
    original_read = store._owner_overview
    original_reconcile = store.notices.reconcile

    def paused_read(*args, **kwargs):
        if current_thread().name.startswith("notice-overview"):
            before_read.set()
            assert allow_read.wait(10)
        return original_read(*args, **kwargs)

    def paused_reconcile(*args, **kwargs):
        if current_thread().name.startswith("notice-overview"):
            before_reconcile.set()
            assert allow_reconcile.wait(10)
        return original_reconcile(*args, **kwargs)

    monkeypatch.setattr(store, "_owner_overview", paused_read)
    monkeypatch.setattr(store.notices, "reconcile", paused_reconcile)
    with ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="notice-overview"
    ) as pool:
        result = pool.submit(store.owner_overview, "owner")
        try:
            assert before_read.wait(10)
            second, token = api.enroll(client)
            allow_read.set()
            assert before_reconcile.wait(10)
            reading = api.sample(observed_at=datetime.now(UTC).isoformat())
            assert api.ingest(client, second, token, [reading]).status_code == 201
            allow_reconcile.set()
            rows = result.result(timeout=10)
        finally:
            allow_read.set()
            allow_reconcile.set()
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT kind FROM telemetry_notices WHERE installation_id=%s AND active=TRUE",
            (second,),
        )
        assert cur.fetchall() == []
    assert [row["id"] for row in rows] == [first]


def test_failed_notice_projection_preserves_valid_ingestion(
    telemetry_client, monkeypatch
):
    client = telemetry_client
    installation, token = api.enroll(client)

    def failed_projection(cur, *args, **kwargs):
        cur.execute("SELECT 1/0")

    with monkeypatch.context() as patch:
        patch.setattr(store.notices, "reconcile", failed_projection)
        response = api.ingest(client, installation, token, [api.sample(value="100")])
    assert response.status_code == 201
    assert response.json["accepted"] == 1
    stored = client.get(f"{api.BASE}/{installation}/samples")
    assert len(stored.json["samples"]) == 1
    assert stored.json["samples"][0]["value"] == "100"
    assert notices(client, installation) == []


def test_projection_failure_cannot_reuse_an_old_acknowledgement(
    telemetry_client, clock, monkeypatch
):
    client = telemetry_client
    installation, token = api.enroll(client)
    deliver(client, installation, token, clock, sequence=1, age_seconds=600)
    first = notices(client, installation)[0]
    assert acknowledge(client, installation, first).status_code == 200

    def failed_projection(cur, *args, **kwargs):
        cur.execute("SELECT 1/0")

    with monkeypatch.context() as patch:
        patch.setattr(store.notices, "reconcile", failed_projection)
        deliver(client, installation, token, clock, sequence=2)
    # The fresh delivery was committed, but its recovery projection failed.
    clock[0] += timedelta(seconds=601)
    deliver(client, installation, token, clock, sequence=3, age_seconds=600)
    assert acknowledge(client, installation, first).status_code == 409
    current = notices(client, installation)[0]
    assert current["attention_required"]
    assert current["episode"] == 2
