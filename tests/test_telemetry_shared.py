# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared views require explicit grants and never infer missing community power."""

import re
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest

from tests import test_i18n

with test_i18n._synthetic_environment():
    import database as db
    from tests import test_dashboard_access_routes as sessions
    from tests import test_telemetry as api
    from tests import test_telemetry_device_faults as faults

app_module = api.app_module
interest_database = api.interest_database
telemetry_client = api.telemetry_client
clock = faults.clock
isolated_settings = faults.isolated_settings
pytestmark = pytest.mark.integration
SUMMARY = api.BASE + "/summary"


@pytest.fixture
def shared(telemetry_client, clock):
    client = telemetry_client
    for actor in ("peer-a", "peer-b"):
        assert db.save_building(
            actor,
            actor + "@example.ch",
            {
                "address": "Private address " + actor,
                "lat": 47,
                "lon": 8,
                "bfs_number": 261,
            },
            {"share_with_neighbors": True},
            verified=True,
        )
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO community_members(community_id,building_id,status,role) VALUES ('community-a',%s,'confirmed','member')",
                (actor,),
            )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "ALTER TABLE telemetry_samples ALTER COLUMN received_at SET DEFAULT %s",
            (clock[0],),
        )
    installations = []
    for index, actor in enumerate(("owner", "peer-a", "peer-b")):
        sessions._set_session(client, actor)
        installation, token = api.enroll(
            client,
            name="Private installation " + actor,
            device_id="device-" + actor,
            source_id="source-" + actor,
        )
        message = api.sample(
            device_id="device-" + actor,
            source_id="source-" + actor,
            value=str(index * 100),
            observed_at=(clock[0] - timedelta(seconds=10)).isoformat(),
        )
        assert api.ingest(client, installation, token, [message]).status_code == 201
        installations.append(
            {"owner": actor, "id": installation, "token": token, "sample": message}
        )
    sessions._set_session(client, "viewer")
    return client, installations, clock


def grant(client, installation, action="share"):
    sessions._set_session(client, installation["owner"])
    response = client.post(
        f"{api.BASE}/{installation['id']}/{action}",
        json={"viewer_building_id": "viewer"},
        headers={"X-CSRF-Token": "csrf-secret"},
    )
    assert response.status_code == 200
    sessions._set_session(client, "viewer")


def test_administrator_has_no_implicit_shared_readings(shared):
    client, installations, _ = shared
    response = client.get(SUMMARY)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.json["sources"] == [] and response.json["groups"] == []
    assert response.json["authorized_installations"] == 0
    assert all(row["id"] not in response.text for row in installations)


def test_three_synchronized_shared_profiles_include_night_zero(shared):
    client, installations, _ = shared
    for row in installations:
        grant(client, row)
    response = client.get(SUMMARY)
    assert response.status_code == 200
    group = response.json["groups"][0]
    assert group["state"] == "available" and group["value"] == "300"
    assert group["contributing_profiles"] == 3
    assert response.json["authorized_installations"] == 3
    assert {row["source_id"] for row in response.json["sources"]} == {
        "source-owner",
        "source-peer-a",
        "source-peer-b",
    }
    for private in (
        "Private installation",
        "Private address",
        "owner_building_id",
        "acknowledged_at",
        "credential",
    ):
        assert private not in response.text


def test_revocation_removes_source_and_withholds_small_sum(shared):
    client, installations, _ = shared
    for row in installations:
        grant(client, row)
    grant(client, installations[0], "unshare")
    response = client.get(SUMMARY)
    assert response.status_code == 200
    assert response.json["authorized_installations"] == 2
    assert installations[0]["id"] not in response.text
    assert response.json["groups"][0]["state"] == "too_small"
    assert response.json["groups"][0]["value"] is None


def test_neighbor_consent_is_required_even_with_explicit_grant(shared):
    client, installations, _ = shared
    for row in installations:
        grant(client, row)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE consents SET share_with_neighbors=FALSE WHERE building_id='owner'"
        )
    response = client.get(SUMMARY)
    assert response.json["authorized_installations"] == 2
    assert installations[0]["id"] not in response.text
    sessions._set_session(client, "owner")
    own = client.get(SUMMARY)
    assert [row["id"] for row in own.json["sources"]] == [installations[0]["id"]]


@pytest.mark.parametrize("change", ["unverified", "left"])
def test_ineligible_owner_is_removed_from_coverage(shared, change):
    client, installations, _ = shared
    for row in installations:
        grant(client, row)
    with db.get_connection() as conn, conn.cursor() as cur:
        if change == "unverified":
            cur.execute("UPDATE buildings SET verified=FALSE WHERE building_id='owner'")
        else:
            cur.execute(
                "UPDATE community_members SET status='left' WHERE building_id='owner'"
            )
    response = client.get(SUMMARY)
    assert response.json["authorized_installations"] == 2
    assert installations[0]["id"] not in response.text
    assert response.json["groups"][0]["value"] is None


def test_rejoining_does_not_restore_revoked_grants(shared):
    client, installations, _ = shared
    for row in installations:
        grant(client, row)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE community_members SET status='left' WHERE building_id='viewer'"
        )
    assert client.get(SUMMARY).status_code == 403
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE community_members SET status='confirmed' WHERE building_id='viewer'"
        )
    assert client.get(SUMMARY).json["authorized_installations"] == 0
    sessions._set_session(client, "other")
    assert client.get(SUMMARY).status_code == 403
    with client.session_transaction() as session:
        session.clear()
    assert client.get(SUMMARY).status_code == 401


def replace_reading(client, row, **overrides):
    message = {**row["sample"], "sample_id": "replacement", "sequence": 2, **overrides}
    assert api.ingest(client, row["id"], row["token"], [message]).status_code == 201


@pytest.mark.parametrize("state", ["stale", "future", "unknown", "estimated"])
def test_unavailable_reading_withholds_sum_without_becoming_zero(shared, state):
    client, installations, clock = shared
    for row in installations:
        grant(client, row)
    row = installations[2]
    if state == "stale":
        clock[0] += timedelta(minutes=11)
    else:
        fields = {"observed_at": (clock[0] - timedelta(seconds=5)).isoformat()}
        if state == "future":
            fields["observed_at"] = (clock[0] + timedelta(seconds=1)).isoformat()
        else:
            fields["quality"] = state
        replace_reading(client, row, **fields)
    response = client.get(SUMMARY)
    group = response.json["groups"][0]
    assert group["value"] is None
    assert group["state"] == "too_small"
    excluded = "estimated" if state == "estimated" else "unavailable"
    assert group["excluded"][excluded] == (3 if state == "stale" else 1)
    source = next(
        source for source in response.json["sources"] if source["id"] == row["id"]
    )
    if state in ("future", "unknown"):
        assert source["samples"][0]["value"] is None
    elif state == "stale":
        assert source["samples"][0]["value"] == "200"
        assert source["samples"][0]["freshness"] == "stale"


def test_source_time_difference_is_disclosed_and_sum_withheld(shared):
    client, installations, clock = shared
    for row in installations:
        grant(client, row)
    replace_reading(
        client,
        installations[2],
        observed_at=(clock[0] - timedelta(seconds=9)).isoformat(),
    )
    group = client.get(SUMMARY).json["groups"][0]
    assert group["state"] == "unsynchronized" and group["value"] is None
    assert group["excluded"]["unsynchronized"] == 3
    assert group["earliest_observed_at"] != group["latest_observed_at"]


def test_overlapping_owner_sources_are_excluded(shared):
    client, installations, _ = shared
    for row in installations:
        grant(client, row)
    sessions._set_session(client, "owner")
    installation, token = api.enroll(
        client, source_id="second-inverter", device_id="second-device"
    )
    message = api.sample(
        source_id="second-inverter",
        device_id="second-device",
        value="300",
        observed_at=installations[0]["sample"]["observed_at"],
    )
    assert api.ingest(client, installation, token, [message]).status_code == 201
    grant(client, {"owner": "owner", "id": installation})
    group = client.get(SUMMARY).json["groups"][0]
    assert group["value"] is None and group["eligible_profiles"] == 2
    assert group["excluded"]["overlapping"] == 1
    assert group["visible_profiles"] == 3


def test_summary_lock_timeout_is_unavailable_then_recovers(shared):
    client, installations, _ = shared
    grant(client, installations[0])
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM telemetry_installations WHERE id=%s FOR UPDATE",
            (installations[0]["id"],),
        )
        response = client.get(SUMMARY)
        assert response.status_code == 503
        assert response.json["error"] == "telemetry_unavailable"
        assert response.headers["Cache-Control"] == "no-store"
    assert client.get(SUMMARY).status_code == 200


@pytest.mark.parametrize(
    "language,title",
    [("de-CH", "Freigegebene Messwerte"), ("fr-CH", "Mesures partagées")],
)
def test_shared_dashboard_localizes_coverage_and_hides_private_fields(
    shared, language, title
):
    client, installations, _ = shared
    for row in installations:
        grant(client, row)
    response = client.get("/leg/community/community-a/telemetry?lang=" + language)
    assert response.status_code == 200
    assert title in response.text
    assert "300 W" in response.text
    assert re.search(r">\s*0 W\s*</p>", response.text)
    assert (
        shared[2][0].astimezone(ZoneInfo("Europe/Zurich")).strftime("%z")
        in response.text
    )
    assert "Private installation" not in response.text
    assert "Private address" not in response.text
    assert "acknowledge" not in response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_shared_dashboard_errors_remain_private(shared):
    client, _, _ = shared
    sessions._set_session(client, "other")
    response = client.get("/leg/community/community-a/telemetry")
    assert response.status_code == 403
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_shared_dashboard_storage_failure_is_unavailable(shared, monkeypatch):
    from telemetry import TelemetryError

    client, _, _ = shared

    def unavailable(*args):
        raise TelemetryError("telemetry_unavailable", 503)

    monkeypatch.setattr(db, "get_shared_telemetry_overview", unavailable)
    response = client.get("/leg/community/community-a/telemetry?lang=fr-CH")
    assert response.status_code == 503
    assert "Mesures actuellement indisponibles" in response.text
    assert response.headers["Cache-Control"] == "no-store"


def test_old_buffered_reading_does_not_replace_current_shared_value(shared):
    client, installations, clock = shared
    for row in installations:
        grant(client, row)
    replace_reading(
        client,
        installations[2],
        value="999",
        observed_at=(clock[0] - timedelta(hours=1)).isoformat(),
    )
    group = client.get(SUMMARY).json["groups"][0]
    assert group["state"] == "available" and group["value"] == "300"


@pytest.mark.parametrize(
    "locale,message",
    [
        ("de-CH", "Keine Leistungswerte im Aufbewahrungszeitraum verfügbar."),
        (
            "fr-CH",
            "Aucune mesure de puissance disponible pendant la durée de conservation.",
        ),
    ],
)
def test_retained_non_power_readings_do_not_claim_the_source_has_no_data(
    telemetry_client, clock, locale, message
):
    client = telemetry_client
    installation, token = api.enroll(client)
    assert (
        api.ingest(
            client,
            installation,
            token,
            [
                api.sample(
                    metric="state_of_charge",
                    unit="%",
                    measurement_location="battery",
                    direction="stored",
                    value="51",
                    observed_at=clock[0].isoformat(),
                )
            ],
        ).status_code
        == 201
    )
    assert (
        client.get(f"{api.BASE}/{installation}/samples").json["samples"][0]["value"]
        == "51"
    )
    response = client.get("/leg/community/community-a/telemetry?lang=" + locale)
    assert response.status_code == 200
    assert "local-reader" in response.text
    assert message in response.text
    assert "Keine Messwerte im Aufbewahrungszeitraum verfügbar." not in response.text
    assert (
        "Aucune mesure disponible pendant la durée de conservation."
        not in response.text
    )
