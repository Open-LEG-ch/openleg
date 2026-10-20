# SPDX-License-Identifier: AGPL-3.0-or-later
"""Device reports remain distinct from missing telemetry and physical diagnosis."""

import os
from datetime import timedelta
from unittest.mock import patch

import pytest

from tests import test_i18n

with test_i18n._synthetic_environment():
    import database as db
    from tests import test_telemetry as api
    from tests import test_telemetry_notices as lifecycle

app_module = api.app_module
interest_database = api.interest_database
telemetry_client = api.telemetry_client
clock = lifecycle.clock
pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def isolated_settings():
    controls = {
        key: os.environ[key]
        for key in ("DATABASE_URL", "MUTANT_UNDER_TEST", "MUTMUT_DEPENDENCY_DEPTH")
        if key in os.environ
    }
    with test_i18n._synthetic_environment(), patch.dict(os.environ, controls):
        yield


def report(client, installation, token, clock, *, sequence, value="1", **fields):
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "ALTER TABLE telemetry_samples ALTER COLUMN received_at SET DEFAULT %s",
            (clock[0],),
        )
    message = api.sample(
        sample_id=f"fault-{sequence}",
        sequence=sequence,
        metric="device_fault",
        unit="state",
        measurement_location="device",
        direction="status",
        value=value,
        observed_at=clock[0].isoformat(),
    )
    message.update(fields)
    return api.ingest(client, installation, token, [message])


def test_reported_fault_acknowledgement_clearance_and_recurrence(
    telemetry_client, clock
):
    client = telemetry_client
    installation, token = api.enroll(client)
    assert report(client, installation, token, clock, sequence=1).status_code == 201
    first = lifecycle.notices(client, installation)[0]
    assert first["kind"] == "device_fault" and first["active"]
    html = client.get("/dashboard/telemetry").text
    assert "Gerät meldet eine Störung" in html
    assert "Prüfen Sie die Anzeige am Gerät" in html
    assert "inverter-1" in html and "local-reader" in html
    assert lifecycle.acknowledge(client, installation, first).status_code == 200
    assert not lifecycle.notices(client, installation)[0]["attention_required"]
    clock[0] += timedelta(seconds=1)
    assert (
        report(client, installation, token, clock, sequence=2, value="0").status_code
        == 201
    )
    cleared = lifecycle.notices(client, installation)[0]
    assert not cleared["active"] and cleared["recovery"] == "device_report_clear"
    assert "Gerät meldet keine Störung mehr" in client.get("/dashboard/telemetry").text
    clock[0] += timedelta(seconds=1)
    assert report(client, installation, token, clock, sequence=3).status_code == 201
    repeated = lifecycle.notices(client, installation)[0]
    assert repeated["id"] == first["id"] and repeated["episode"] == 2
    assert repeated["attention_required"] and repeated["acknowledged_at"] is None
    assert lifecycle.acknowledge(client, installation, first).status_code == 409
    assert client.get(f"{api.BASE}/{installation}/aggregates").json["aggregates"] == []
    assert (
        "L’appareil signale un défaut"
        in client.get("/dashboard/telemetry?lang=fr-CH").text
    )


@pytest.mark.parametrize("state", ["stale", "future", "unknown"])
def test_unavailable_report_does_not_assert_active_or_cleared_fault(
    telemetry_client, clock, state
):
    client = telemetry_client
    installation, token = api.enroll(client)
    fields = {}
    if state == "unknown":
        fields["quality"] = "unknown"
    elif state == "future":
        clock[0] += timedelta(seconds=1)
    observed = clock[0]
    assert (
        report(client, installation, token, clock, sequence=1, **fields).status_code
        == 201
    )
    if state == "stale":
        clock[0] += timedelta(seconds=61)
    elif state == "future":
        clock[0] -= timedelta(seconds=1)
    current = lifecycle.notices(client, installation)
    assert not any(row["kind"] == "device_fault" and row["active"] for row in current)
    assert not any(row["recovery"] == "device_report_clear" for row in current)
    assert observed.isoformat() in client.get("/dashboard/telemetry").text


@pytest.mark.parametrize(
    "fields",
    [
        {"value": "2"},
        {"value": "0.5"},
        {"quality": "estimated"},
        {"unit": "W"},
        {"measurement_location": "site"},
        {"direction": "generation"},
        {"measurement_location": "pv_inverter", "direction": "generation"},
        {"metric": "power", "unit": "W"},
    ],
)
def test_fault_contract_rejects_nonboolean_estimated_or_misplaced_reports(
    telemetry_client, clock, fields
):
    client = telemetry_client
    installation, token = api.enroll(client)
    payload = api.sample(
        metric="device_fault",
        unit="state",
        measurement_location="device",
        direction="status",
        value="1",
    )
    payload.update(fields)
    assert api.ingest(client, installation, token, [payload]).status_code == 400
    assert client.get(f"{api.BASE}/{installation}/samples").json["samples"] == []


def test_existing_notice_constraints_upgrade_without_losing_rows(
    telemetry_client, clock
):
    client = telemetry_client
    installation, token = api.enroll(client)
    previous = lifecycle.notices(client, installation)[0]
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            ALTER TABLE telemetry_notices DROP CONSTRAINT telemetry_notices_kind_check;
            ALTER TABLE telemetry_notices ADD CONSTRAINT telemetry_notices_kind_check
                CHECK (kind IN ('collector_late','source_missing','source_old','source_channel_missing','source_unknown','source_clock'));
            ALTER TABLE telemetry_notices DROP CONSTRAINT telemetry_notices_recovery_check;
            ALTER TABLE telemetry_notices ADD CONSTRAINT telemetry_notices_recovery_check
                CHECK (recovery IN ('readings_current','condition_changed'));
        """)
    db.create_tables()
    db.create_tables()
    assert report(client, installation, token, clock, sequence=1).status_code == 201
    rows = lifecycle.notices(client, installation)
    assert any(row["id"] == previous["id"] for row in rows)
    assert any(row["kind"] == "device_fault" and row["active"] for row in rows)


@pytest.mark.parametrize("quality,age", [("unknown", 0), ("measured", 61)])
def test_unavailable_clear_report_is_not_confirmed_recovery(
    telemetry_client, clock, quality, age
):
    client = telemetry_client
    installation, token = api.enroll(client)
    assert report(client, installation, token, clock, sequence=1).status_code == 201
    clock[0] += timedelta(seconds=120)
    assert (
        report(
            client,
            installation,
            token,
            clock,
            sequence=2,
            value="0",
            quality=quality,
            observed_at=(clock[0] - timedelta(seconds=age)).isoformat(),
        ).status_code
        == 201
    )
    previous = next(
        row
        for row in lifecycle.notices(client, installation)
        if row["kind"] == "device_fault"
    )
    assert not previous["active"] and previous["recovery"] == "condition_changed"
    assert (
        "Gerät meldet keine Störung mehr" not in client.get("/dashboard/telemetry").text
    )


def test_duplicate_and_buffered_clear_do_not_silence_latest_fault(
    telemetry_client, clock
):
    client = telemetry_client
    installation, token = api.enroll(client)
    assert report(client, installation, token, clock, sequence=1).status_code == 201
    first = lifecycle.notices(client, installation)[0]
    assert (
        report(client, installation, token, clock, sequence=1).json["duplicates"] == 1
    )
    assert (
        report(
            client,
            installation,
            token,
            clock,
            sequence=2,
            value="0",
            observed_at=(clock[0] - timedelta(seconds=10)).isoformat(),
        ).status_code
        == 201
    )
    latest = lifecycle.notices(client, installation)[0]
    assert latest["active"] and latest["episode"] == first["episode"]


def test_sample_sharing_does_not_share_fault_acknowledgements(telemetry_client, clock):
    client = telemetry_client
    installation, token = api.enroll(client)
    assert report(client, installation, token, clock, sequence=1).status_code == 201
    fault = lifecycle.notices(client, installation)[0]
    assert (
        client.post(
            f"{api.BASE}/{installation}/share",
            json={"viewer_building_id": "viewer"},
            headers={"X-CSRF-Token": "csrf-secret"},
        ).status_code
        == 200
    )
    lifecycle.sessions._set_session(client, "viewer")
    assert client.get(f"{api.BASE}/{installation}/samples").status_code == 200
    assert client.get(f"{api.BASE}/{installation}/notices").status_code == 403
    assert lifecycle.acknowledge(client, installation, fault).status_code == 403


def test_explicit_clearance_after_unknown_span_is_reported_once(
    telemetry_client, clock
):
    client = telemetry_client
    installation, token = api.enroll(client)
    assert report(client, installation, token, clock, sequence=1).status_code == 201
    fault = lifecycle.notices(client, installation)[0]
    clock[0] += timedelta(seconds=61)
    uncertain = next(
        row
        for row in lifecycle.notices(client, installation)
        if row["id"] == fault["id"]
    )
    assert not uncertain["active"] and uncertain["recovery"] == "condition_changed"
    clock[0] += timedelta(seconds=1)
    assert (
        report(client, installation, token, clock, sequence=2, value="0").status_code
        == 201
    )
    cleared = next(
        row
        for row in lifecycle.notices(client, installation)
        if row["id"] == fault["id"]
    )
    assert cleared["recovery"] == "device_report_clear"
    assert cleared["resolved_at"] != uncertain["resolved_at"]
    assert "Gerät meldet keine Störung mehr" in client.get("/dashboard/telemetry").text
    clock[0] += timedelta(seconds=1)
    repeated = next(
        row
        for row in lifecycle.notices(client, installation)
        if row["id"] == fault["id"]
    )
    assert repeated["resolved_at"] == cleared["resolved_at"]


def test_clearance_does_not_resurrect_expired_notice_history(telemetry_client, clock):
    client = telemetry_client
    installation, token = api.enroll(client, raw_retention_days=1)
    assert report(client, installation, token, clock, sequence=1).status_code == 201
    fault = lifecycle.notices(client, installation)[0]
    clock[0] += timedelta(seconds=61)
    uncertain = next(
        row
        for row in lifecycle.notices(client, installation)
        if row["id"] == fault["id"]
    )
    clock[0] += timedelta(days=1, seconds=1)
    assert (
        report(client, installation, token, clock, sequence=2, value="0").status_code
        == 201
    )
    assert not any(
        row["id"] == fault["id"] for row in lifecycle.notices(client, installation)
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT recovery,resolved_at FROM telemetry_notices WHERE id=%s",
            (fault["id"],),
        )
        old = cur.fetchone()
    assert old["recovery"] == "condition_changed"
    assert old["resolved_at"].isoformat() == uncertain["resolved_at"]
