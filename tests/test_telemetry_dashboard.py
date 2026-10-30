# SPDX-License-Identifier: AGPL-3.0-or-later
"""Owner readings through the dashboard route and real telemetry storage."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import dashboard
import database as db
from store import telemetry as store
from tests import test_dashboard_access_routes as sessions
from tests import test_interest_postgres
from tests import test_telemetry as api

app_module = api.app_module
interest_database = api.interest_database
telemetry_client = api.telemetry_client
pytestmark = pytest.mark.integration


def test_owner_can_read_night_production_with_source_and_time(telemetry_client):
    client = telemetry_client
    installation, token = api.enroll(client)
    observed = datetime.now(UTC) - timedelta(seconds=15)
    assert (
        api.ingest(
            client,
            installation,
            token,
            [api.sample(value="0", observed_at=observed.isoformat())],
        ).status_code
        == 201
    )

    response = client.get("/dashboard/telemetry")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    html = response.text
    for visible in ("Roof", "local-reader", "inverter-1", "0 W", "Gemessen", "Aktuell"):
        assert visible in html
    assert observed.isoformat() in html
    assert token not in html


def test_owner_readings_follow_the_selected_language(telemetry_client):
    client = telemetry_client
    installation, token = api.enroll(client)
    assert (
        api.ingest(client, installation, token, [api.sample(value="0")]).status_code
        == 201
    )
    response = client.get("/dashboard/telemetry?lang=fr-CH")
    assert response.headers["Content-Language"] == "fr-CH"
    assert "Mes mesures" in response.text
    assert "Production photovoltaïque" in response.text
    assert "À jour" in response.text
    assert "0 W" in response.text
    assert "local-reader" in response.text
    response = client.get("/dashboard/telemetry?lang=de-CH")
    assert "Meine Messwerte" in response.text
    assert "Aktuell" in response.text


def test_one_second_cadence_uses_the_selected_singular_form(telemetry_client):
    client = telemetry_client
    api.enroll(client, cadence_seconds=1)
    french = client.get("/dashboard/telemetry?lang=fr-CH").text
    assert "1 seconde" in french
    assert "1 secondes" not in french
    html = client.get("/dashboard/telemetry?lang=de-CH").text
    assert "1 Sekunde" in html
    assert "1 Sekunden" not in html


def test_channels_stay_separate_and_unknown_time_is_not_a_current_zero(
    telemetry_client,
    monkeypatch,
):
    client = telemetry_client
    installation, token = api.enroll(client)
    now = datetime.now(UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    monkeypatch.setattr(store, "datetime", Clock)
    observed = now - timedelta(seconds=10)
    channels = [
        api.sample(value="800", observed_at=observed.isoformat()),
        api.sample(
            sample_id="grid-import",
            sequence=2,
            value="250",
            measurement_location="grid_connection",
            direction="import",
            observed_at=observed.isoformat(),
            quality="estimated",
        ),
        api.sample(
            sample_id="grid-export",
            sequence=3,
            value="50",
            measurement_location="grid_connection",
            direction="export",
            observed_at=observed.isoformat(),
        ),
        api.sample(
            sample_id="battery",
            sequence=4,
            value="62",
            metric="state_of_charge",
            unit="%",
            measurement_location="battery",
            direction="stored",
            observed_at=observed.isoformat(),
        ),
        api.sample(
            sample_id="no-time",
            sequence=5,
            value="99999",
            measurement_location="site",
            direction="consumption",
            observed_at=None,
        ),
    ]
    assert api.ingest(client, installation, token, channels).status_code == 201

    view = store.owner_overview("owner")
    assert len(view) == 1
    samples = view[0]["samples"]
    assert len(samples) == 5
    assert all(row["age_seconds"] == 10 for row in samples if row["observed_at"])
    html = client.get("/dashboard/telemetry").text
    for label in (
        "PV-Erzeugung",
        "Netzbezug",
        "Netzeinspeisung",
        "Batteriestand",
        "Geschätzt",
    ):
        assert label in html
    for value in ("800 W", "250 W", "50 W", "62 %"):
        assert value in html
    assert "99999" not in html
    assert "Aktualität unbekannt" in html
    assert "Standortverbrauch" in html
    assert "Messzeit unbekannt" in html


def test_recent_delivery_and_replay_cannot_refresh_an_old_source(telemetry_client):
    client = telemetry_client
    installation, token = api.enroll(client)
    observed = datetime.now(UTC) - timedelta(minutes=10)
    old = api.sample(observed_at=observed.isoformat())
    assert api.ingest(client, installation, token, [old]).status_code == 201
    first = store.owner_overview("owner")[0]
    assert first["delivery_status"] == "recent"
    assert first["samples"][0]["freshness"] == "stale"
    assert first["samples"][0]["age_seconds"] >= 600

    replay = api.ingest(client, installation, token, [old])
    assert replay.json["duplicates"] == 1
    second = store.owner_overview("owner")[0]
    assert second["samples"][0]["received_at"] == first["samples"][0]["received_at"]
    assert second["samples"][0]["observed_at"] == observed.isoformat()
    html = client.get("/dashboard/telemetry").text
    assert "Veraltet" in html
    assert "Messquelle liefert ältere Werte" in html
    assert "Stromausfall" not in html


def test_readings_are_discoverable_from_the_owner_dashboard(telemetry_client):
    response = telemetry_client.get("/dashboard")
    assert response.status_code == 200
    assert 'href="/dashboard/telemetry"' in response.text


def test_dashboard_uses_only_the_signed_in_active_owner(telemetry_client):
    client = telemetry_client
    installation, token = api.enroll(client, name="Private roof")
    assert (
        api.ingest(
            client, installation, token, [api.sample(value="987.654")]
        ).status_code
        == 201
    )
    assert (
        api.change(
            client, installation, "share", {"viewer_building_id": "viewer"}
        ).status_code
        == 200
    )
    for actor in ("viewer", "other"):
        sessions._set_session(client, actor)
        response = client.get("/dashboard/telemetry?bid=owner")
        assert response.status_code == 200
        assert "Private roof" not in response.text
        assert "987.654" not in response.text
        assert store.owner_overview(actor) == []
    with client.session_transaction() as state:
        state.clear()
    response = client.get("/dashboard/telemetry?bid=owner")
    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "no-store"
    assert "Private roof" not in response.text


@pytest.mark.parametrize("revocation", ["membership", "verification", "deletion"])
def test_owner_access_disappears_after_revocation(telemetry_client, revocation):
    client = telemetry_client
    installation, token = api.enroll(client)
    assert api.ingest(client, installation, token, [api.sample()]).status_code == 201
    if revocation == "deletion":
        assert (
            client.delete(
                f"{api.BASE}/{installation}", headers={"X-CSRF-Token": "csrf-secret"}
            ).status_code
            == 200
        )
    else:
        with db.get_connection() as conn, conn.cursor() as cur:
            if revocation == "membership":
                cur.execute(
                    "UPDATE community_members SET status='invited' WHERE building_id='owner'"
                )
            else:
                cur.execute(
                    "UPDATE buildings SET verified=FALSE WHERE building_id='owner'"
                )
    assert store.owner_overview("owner") == []
    response = client.get("/dashboard/telemetry")
    assert response.status_code == 200
    assert "Roof" not in response.text


@pytest.mark.parametrize("cadence,expected", [(30, "stale"), (300, "fresh")])
def test_source_cadence_sets_freshness(telemetry_client, cadence, expected):
    client = telemetry_client
    installation, token = api.enroll(client, cadence_seconds=cadence)
    assert (
        api.ingest(
            client,
            installation,
            token,
            [
                api.sample(
                    cadence_seconds=cadence,
                    observed_at=(
                        datetime.now(UTC) - timedelta(seconds=100)
                    ).isoformat(),
                )
            ],
        ).status_code
        == 201
    )
    assert store.owner_overview("owner")[0]["samples"][0]["freshness"] == expected


def test_late_delivery_is_distinct_from_an_old_reading(telemetry_client, monkeypatch):
    client = telemetry_client
    installation, token = api.enroll(client)
    assert api.ingest(client, installation, token, [api.sample()]).status_code == 201
    later = datetime.now(UTC) + timedelta(minutes=10)

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return later.astimezone(tz)

    monkeypatch.setattr(store, "datetime", Later)
    view = store.owner_overview("owner")[0]
    assert view["delivery_status"] == "late"
    assert view["samples"][0]["freshness"] == "stale"
    html = client.get("/dashboard/telemetry").text
    assert "Keine neue Kollektor-Lieferung" in html
    assert "Messquelle liefert ältere Werte" not in html


def test_another_channel_cannot_make_a_missing_channel_look_recent(telemetry_client):
    client = telemetry_client
    installation, token = api.enroll(client)
    old = datetime.now(UTC) - timedelta(minutes=10)
    assert (
        api.ingest(
            client, installation, token, [api.sample(observed_at=old.isoformat())]
        ).status_code
        == 201
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE telemetry_samples SET received_at=CURRENT_TIMESTAMP-INTERVAL '10 minutes' WHERE installation_id=%s",
            (installation,),
        )
    assert (
        api.ingest(
            client,
            installation,
            token,
            [
                api.sample(
                    sample_id="battery-now",
                    sequence=2,
                    metric="state_of_charge",
                    unit="%",
                    measurement_location="battery",
                    direction="stored",
                    value="60",
                )
            ],
        ).status_code
        == 201
    )
    view = store.owner_overview("owner")[0]
    assert view["delivery_status"] == "recent"
    power = next(row for row in view["samples"] if row["metric"] == "power")
    assert power["delivery_status"] == "late"
    html = client.get("/dashboard/telemetry").text
    assert "Messquelle liefert ältere Werte" not in html
    assert "Für diesen Messkanal fehlen neue Daten" in html


def test_buffered_old_delivery_updates_channel_status_without_replacing_value(
    telemetry_client,
):
    client = telemetry_client
    installation, token = api.enroll(client)
    now = datetime.now(UTC)
    assert (
        api.ingest(
            client,
            installation,
            token,
            [
                api.sample(
                    value="321", observed_at=(now - timedelta(minutes=10)).isoformat()
                )
            ],
        ).status_code
        == 201
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE telemetry_samples SET received_at=CURRENT_TIMESTAMP-INTERVAL '10 minutes' WHERE installation_id=%s",
            (installation,),
        )
    assert (
        api.ingest(
            client,
            installation,
            token,
            [
                api.sample(
                    sample_id="buffered-older",
                    sequence=2,
                    value="111",
                    observed_at=(now - timedelta(minutes=15)).isoformat(),
                )
            ],
        ).status_code
        == 201
    )
    row = store.owner_overview("owner")[0]["samples"][0]
    assert row["value"] == "321"
    assert row["freshness"] == "stale"
    assert row["delivery_status"] == "recent"


def test_expired_buffered_delivery_cannot_make_a_channel_look_recent(
    telemetry_client, monkeypatch
):
    client = telemetry_client
    installation, token = api.enroll(client, raw_retention_days=1)
    now = datetime.now(UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    monkeypatch.setattr(store, "datetime", Clock)
    assert (
        api.ingest(
            client,
            installation,
            token,
            [
                api.sample(
                    value="321", observed_at=(now - timedelta(minutes=10)).isoformat()
                ),
                api.sample(
                    sample_id="almost-expired",
                    sequence=2,
                    value="111",
                    observed_at=(
                        now - timedelta(days=1) + timedelta(seconds=10)
                    ).isoformat(),
                ),
            ],
        ).status_code
        == 201
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE telemetry_samples SET received_at=%s WHERE installation_id=%s AND sequence=1",
            (now - timedelta(minutes=10), installation),
        )

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return (now + timedelta(seconds=20)).astimezone(tz)

    monkeypatch.setattr(store, "datetime", Later)
    view = store.owner_overview("owner")[0]
    assert view["delivery_status"] == "late"
    assert len(view["samples"]) == 1
    assert view["samples"][0]["value"] == "321"
    assert view["samples"][0]["delivery_status"] == "late"


def test_buffered_older_values_do_not_replace_the_latest_observation(telemetry_client):
    client = telemetry_client
    installation, token = api.enroll(client)
    now = datetime.now(UTC)
    assert (
        api.ingest(
            client,
            installation,
            token,
            [
                api.sample(
                    value="456", observed_at=(now - timedelta(seconds=15)).isoformat()
                ),
                api.sample(
                    sample_id="buffered",
                    sequence=2,
                    value="999",
                    observed_at=(now - timedelta(hours=1)).isoformat(),
                ),
            ],
        ).status_code
        == 201
    )
    rows = store.owner_overview("owner")[0]["samples"]
    assert len(rows) == 1
    assert rows[0]["value"] == "456"
    assert rows[0]["freshness"] == "fresh"
    html = client.get("/dashboard/telemetry").text
    assert "456 W" in html
    assert "999 W" not in html


@pytest.mark.parametrize("quality,offset", [("unknown", -10), ("measured", 120)])
def test_unknown_quality_and_future_clocks_hide_numeric_values(
    telemetry_client, quality, offset
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
                    value="777.777",
                    quality=quality,
                    observed_at=(
                        datetime.now(UTC) + timedelta(seconds=offset)
                    ).isoformat(),
                )
            ],
        ).status_code
        == 201
    )
    html = client.get("/dashboard/telemetry").text
    assert "777.777" not in html
    assert "Kein aktueller Wert" in html


def test_retention_hides_expired_readings_before_cleanup(telemetry_client):
    client = telemetry_client
    installation, token = api.enroll(client, raw_retention_days=1)
    assert (
        api.ingest(
            client, installation, token, [api.sample(value="555.555")]
        ).status_code
        == 201
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE telemetry_samples SET observed_at=CURRENT_TIMESTAMP-INTERVAL '2 days' WHERE installation_id=%s",
            (installation,),
        )
    assert store.owner_overview("owner")[0]["samples"] == []
    html = client.get("/dashboard/telemetry").text
    assert "555.555" not in html
    assert "Keine Messwerte im Aufbewahrungszeitraum" in html


@pytest.mark.parametrize(
    "instant,expected",
    [
        ("2026-10-25T00:30:00+00:00", "25.10.2026 02:30:00 CEST (+0200)"),
        ("2026-10-25T01:30:00+00:00", "25.10.2026 02:30:00 CET (+0100)"),
    ],
)
def test_swiss_local_times_distinguish_the_repeated_dst_hour(instant, expected):
    result = dashboard.telemetry_view([{"samples": [{"observed_at": instant}]}])
    assert result[0]["samples"][0]["observed_label"] == expected
    assert result[0]["samples"][0]["observed_at"] == instant


def test_unavailable_storage_does_not_render_zero_or_private_errors(
    telemetry_client, monkeypatch, caplog
):
    def unavailable():
        raise RuntimeError("private-connection-material")

    monkeypatch.setattr(db, "get_connection", unavailable)
    response = telemetry_client.get("/dashboard/telemetry")
    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"
    assert "0 W" not in response.text
    assert "private-connection-material" not in response.text + caplog.text


def test_blocked_storage_returns_unavailable_and_recovers(
    telemetry_client, monkeypatch
):
    client = telemetry_client
    api.enroll(client)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_backend_pid() AS pid")
        blocker = SimpleNamespace(pid=cur.fetchone()["pid"])
        cur.execute("LOCK TABLE telemetry_samples IN ACCESS EXCLUSIVE MODE")
        with test_interest_postgres.transaction_race(monkeypatch) as race:
            request = race.start(lambda: client.get("/dashboard/telemetry"))
            race.wait_until_blocked_by(request, blocker)
            response = race.finish(request)
    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"
    assert "0 W" not in response.text
    assert client.get("/dashboard/telemetry").status_code == 200
