# SPDX-License-Identifier: AGPL-3.0-or-later
"""Metering fixtures pass through the ordinary E66 evidence and job commands."""

import gzip
import io
import re
from contextlib import contextmanager

import psycopg2
import pytest
from babel.numbers import format_decimal
from psycopg2.extensions import make_dsn

from tests import test_sandbox_http
from tests.test_sandbox_personas import _start

village = test_sandbox_http.village
_csrf = test_sandbox_http._csrf
pytestmark = pytest.mark.integration


@contextmanager
def _workspace_connection(village, prefix):
    """Connect to the database owned by the selected visitor workspace."""
    manager = village.extensions["villages"]
    workspace = manager.workspaces[prefix.rsplit("/", 1)[1]]
    with psycopg2.connect(
        make_dsn(manager.postgres.dsn, dbname=workspace["name"])
    ) as connection:
        yield connection


def test_operator_imports_a_complete_month_and_replay_preserves_evidence(village):
    """A repeated complete source preserves its evidence and reading totals."""
    client = village.test_client()
    prefix, _ = _start(client, "operator")
    path = prefix + "/sandbox/metering"
    page = client.get(path)
    assert page.status_code == 200
    assert "2880" in page.text and "Europe/Zurich" in page.text
    assert "01.09.2026 00:00" in page.text and "01.10.2026 00:00" in page.text
    fixture = client.get(path + "/fixtures/valid.xml.gz")
    assert fixture.status_code == 200
    assert client.get(path + "/fixtures/duplicate.xml.gz").data == fixture.data
    source = gzip.decompress(fixture.data)
    assert b"SIMULATION SONNWIL" in source and b"2026-08-31T22:00:00Z" in source
    for attempt in range(2):
        response = client.post(
            path + "/import",
            data={
                "csrf_token": _csrf(page),
                "delivery": (io.BytesIO(fixture.data), "simulation-sonnwil.xml.gz"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert ("Bereits importiert" if attempt else "Angenommen") in response.text
        assert format_decimal(14400, locale="de_CH") in response.text
        production = re.search(
            r"<tr><th[^>]*>SONNWIL-SIM-P1</th>(.*?)</tr>", response.text, re.DOTALL
        )
        assert production and format_decimal(1440, locale="de_CH") in production.group(
            1
        )
        page = response
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT COUNT(*) FROM metering_point_readings WHERE measured_at >= '2026-08-31T22:00:00Z'"
        )
        assert cursor.fetchone()[0] == 14400
        cursor.execute(
            "SELECT COUNT(*) FROM sdat_imports WHERE period_start = '2026-08-31T22:00:00Z'"
        )
        assert cursor.fetchone()[0] == 1
        cursor.execute(
            "SELECT COUNT(*), MIN(status), MIN(lifecycle_state) FROM (SELECT d.status, COALESCE(e.action,'current') lifecycle_state FROM vnb_calculated_values_deliveries d LEFT JOIN vnb_calculated_values_events e ON e.delivery_id=d.id WHERE d.period_start = '2026-08-31T22:00:00Z') d"
        )
        assert cursor.fetchone() == (1, "accepted", "current")
        cursor.execute(
            "SELECT direction, SUM(total_kwh), SUM(community_kwh), SUM(grid_kwh) FROM metering_point_readings WHERE measured_at >= '2026-08-31T22:00:00Z' GROUP BY direction ORDER BY direction"
        )
        assert cursor.fetchall() == [
            ("consumption", 2880, 1440, 1440),
            ("production", 1440, 1440, 0),
        ]


def _upload(client, path, page, payload, filename="simulation-sonnwil.xml.gz"):
    """Upload evidence using the current page's CSRF token."""
    return client.post(
        path + "/import",
        data={"csrf_token": _csrf(page), "delivery": (io.BytesIO(payload), filename)},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def test_refused_sources_never_create_readings_or_accepted_evidence(village):
    """Missing, unmapped and overprecise sources leave accepted storage empty."""
    client = village.test_client()
    prefix, _ = _start(client, "operator")
    path = prefix + "/sandbox/metering"
    page = client.get(path)
    for kind, message in (
        ("missing", "Ein Viertelstundenwert fehlt"),
        ("unmapped", "Ein Messpunkt ist nicht zugeordnet"),
        ("precision", "feinere Werte"),
    ):
        delivery = client.get(path + "/fixtures/" + kind + ".xml.gz")
        assert delivery.status_code == 200
        response = _upload(client, path, page, delivery.data)
        assert response.status_code == 422 and message in response.text
        page = response
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        for table in (
            "metering_point_readings",
            "sdat_imports",
            "vnb_calculated_values_deliveries",
        ):
            column = (
                "measured_at" if table == "metering_point_readings" else "period_start"
            )
            cursor.execute(
                "SELECT COUNT(*) FROM "
                + table
                + " WHERE "
                + column
                + " >= '2026-08-31T22:00:00Z'"
            )
            assert cursor.fetchone()[0] == 0


def test_correction_requires_explicit_withdrawal_and_preserves_original(village):
    """Correction retains the withdrawn source and replaces reading totals once."""
    client = village.test_client()
    prefix, _ = _start(client, "operator")
    path = prefix + "/sandbox/metering"
    page = client.get(path)
    original = client.get(path + "/fixtures/valid.xml.gz").data
    correction = client.get(path + "/fixtures/corrected.xml.gz").data
    page = _upload(client, path, page, original)
    response = _upload(client, path, page, correction)
    assert (
        response.status_code == 422
        and "Originalquelle ausdrücklich zurück" in response.text
    )
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT id, evidence_sha256 FROM vnb_calculated_values_deliveries WHERE period_start = '2026-08-31T22:00:00Z'"
        )
        original_id, original_hash = cursor.fetchone()
    withdrawal = path + "/withdraw/" + str(original_id)
    assert (
        client.post(
            withdrawal,
            data={"csrf_token": _csrf(page), "reason": "SIMULATION Ersatzquelle"},
        ).status_code
        == 400
    )
    fields = {
        "csrf_token": _csrf(page),
        "reason": "SIMULATION Ersatzquelle",
        "confirm": "yes",
    }
    assert client.post(withdrawal, data=fields).status_code == 303
    assert client.post(withdrawal, data=fields).status_code == 303
    replay = _upload(client, path, page, original)
    assert replay.status_code == 200 and "Zurückgezogen" in replay.text
    corrected = _upload(client, path, replay, correction)
    assert corrected.status_code == 200 and "Angenommen" in corrected.text
    production = re.search(
        r"<tr><th[^>]*>SONNWIL-SIM-P1</th>(.*?)</tr>", corrected.text, re.DOTALL
    )
    assert production
    cells = re.findall(r"<td[^>]*>(.*?)</td>", production.group(1), re.DOTALL)
    assert cells[1:] == [
        format_decimal(value, locale="de_CH") for value in (2880, 1728, 1728, 0)
    ]
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT COUNT(*) FROM vnb_calculated_values_events WHERE delivery_id=%s",
            (original_id,),
        )
        assert cursor.fetchone()[0] == 1
        cursor.execute(
            "SELECT id, evidence_sha256, source_metadata FROM vnb_calculated_values_deliveries WHERE period_start = '2026-08-31T22:00:00Z' ORDER BY id"
        )
        sources = cursor.fetchall()
        assert (
            len(sources) == 2
            and sources[0][1] == original_hash
            and sources[1][1] != original_hash
        )
        assert sources[1][2]["previously_withdrawn_deliveries"][0]["id"] == original_id
        cursor.execute(
            "SELECT SUM(total_kwh), SUM(community_kwh), SUM(grid_kwh) FROM metering_point_readings WHERE measured_at >= '2026-08-31T22:00:00Z' AND direction='consumption'"
        )
        assert cursor.fetchone() == (2880, 1728, 1152)
        cursor.execute(
            "SELECT SUM(total_kwh) FROM metering_point_readings WHERE measured_at >= '2026-08-31T22:00:00Z' AND direction='production'"
        )
        assert cursor.fetchone()[0] == 1728
        cursor.execute(
            "SELECT COUNT(*) FROM metering_point_readings WHERE measured_at >= '2026-08-31T22:00:00Z'"
        )
        assert cursor.fetchone()[0] == 14400
        cursor.execute(
            "SELECT COUNT(*) FROM sdat_imports WHERE period_start = '2026-08-31T22:00:00Z'"
        )
        assert cursor.fetchone()[0] == 2


def test_confined_scheduled_failure_can_retry_without_duplicate_delivery(village):
    """A failed local job retries through normal scheduling without duplicate data."""
    client = village.test_client()
    prefix, _ = _start(client, "operator")
    path = prefix + "/sandbox/metering"
    page = client.get(path)
    failure = client.post(
        path + "/jobs",
        data={"csrf_token": _csrf(page), "job_action": "due", "fail_source": "yes"},
        follow_redirects=True,
    )
    assert failure.status_code == 200 and "Abruf fehlgeschlagen" in failure.text
    repeat_tick = client.post(
        path + "/jobs",
        data={"csrf_token": _csrf(failure), "job_action": "due"},
        follow_redirects=True,
    )
    assert repeat_tick.status_code == 200
    retry = client.post(
        path + "/jobs",
        data={"csrf_token": _csrf(repeat_tick), "job_action": "retry"},
        follow_redirects=True,
    )
    assert retry.status_code == 200 and "Erfolgreich" in retry.text
    again = client.post(
        path + "/jobs",
        data={"csrf_token": _csrf(retry), "job_action": "retry"},
        follow_redirects=True,
    )
    assert again.status_code == 200
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT status, attempts, imported_files, imported_readings FROM sdat_ingestion_runs ORDER BY id"
        )
        assert cursor.fetchall() == [
            ("failure", 1, 0, 0),
            ("success", 1, 1, 14400),
            ("success", 1, 0, 0),
        ]
        cursor.execute(
            "SELECT COUNT(*) FROM vnb_calculated_values_deliveries WHERE period_start = '2026-08-31T22:00:00Z'"
        )
        assert cursor.fetchone()[0] == 1
        cursor.execute(
            "SELECT COUNT(*) FROM sdat_imports WHERE period_start = '2026-08-31T22:00:00Z'"
        )
        assert cursor.fetchone()[0] == 1
        cursor.execute(
            "SELECT COUNT(*) FROM metering_point_readings WHERE measured_at >= '2026-08-31T22:00:00Z'"
        )
        assert cursor.fetchone()[0] == 14400


def test_role_csrf_and_attempt_urls_cannot_cross_villages(village):
    """Writes and saved diagnostics enforce role, CSRF and visitor scope."""
    from tests.test_sandbox_personas import _switch

    client, other = village.test_client(), village.test_client()
    prefix, _ = _start(client, "operator")
    other_prefix, _ = _start(other, "operator")
    path = prefix + "/sandbox/metering"
    page = client.get(path)
    delivery = client.get(path + "/fixtures/valid.xml.gz").data
    assert (
        client.post(
            path + "/import",
            data={"delivery": (io.BytesIO(delivery), "synthetic.gz")},
            content_type="multipart/form-data",
        ).status_code
        == 400
    )
    imported = _upload(client, path, page, delivery)
    attempt = imported.request.args["attempt"]
    assert (
        other.get(other_prefix + "/sandbox/metering?attempt=" + attempt).status_code
        == 404
    )
    assert client.get(path + "?attempt=../../mail").status_code == 404
    with (
        _workspace_connection(village, other_prefix) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT COUNT(*) FROM metering_point_readings WHERE measured_at >= '2026-08-31T22:00:00Z'"
        )
        assert cursor.fetchone()[0] == 0
    resident = _switch(client, prefix, "resident")
    assert client.get(path).status_code == 403
    assert client.get(path + "/fixtures/valid.xml.gz").status_code == 403
    assert (
        client.post(
            path + "/import",
            data={
                "csrf_token": _csrf(resident),
                "delivery": (io.BytesIO(delivery), "synthetic.gz"),
            },
            content_type="multipart/form-data",
        ).status_code
        == 403
    )
    assert (
        client.post(
            path + "/jobs", data={"csrf_token": _csrf(resident), "job_action": "due"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            path + "/withdraw/1",
            data={
                "csrf_token": _csrf(resident),
                "confirm": "yes",
                "reason": "SIMULATION",
            },
        ).status_code
        == 403
    )
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT COUNT(*) FROM vnb_calculated_values_events")
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT COUNT(*) FROM sdat_ingestion_runs")
        assert cursor.fetchone()[0] == 0


def test_malformed_oversized_and_foreign_sources_stay_refused(village):
    """Unsafe or foreign evidence never bypasses the ordinary intake guards."""
    client = village.test_client()
    prefix, _ = _start(client, "operator")
    path = prefix + "/sandbox/metering"
    page = client.get(path)
    valid = gzip.decompress(client.get(path + "/fixtures/valid.xml.gz").data)
    foreign = gzip.compress(
        valid.replace(b"SONNWIL-SIMULATION-LEG", b"SONNWIL-OTHER-COMMUNITY")
    )
    for payload, text in (
        (gzip.compress(b"x" * (8 * 1024 * 1024 + 1)), "Limit von 8 MiB"),
        (b"\x1f\x8bbroken", "kein unterstützter vollständiger E66-Beleg"),
        (
            bytes.fromhex("1f8b08000000000000ff070000000000000000"),
            "kein unterstützter vollständiger E66-Beleg",
        ),
        (foreign, "nicht zur vorbereiteten LEG Sonnwil"),
    ):
        response = _upload(client, path, page, payload)
        assert response.status_code == 422 and text in response.text
        page = response
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        for table in (
            "metering_point_readings",
            "sdat_imports",
            "vnb_calculated_values_deliveries",
        ):
            column = (
                "measured_at" if table == "metering_point_readings" else "period_start"
            )
            cursor.execute(
                "SELECT COUNT(*) FROM "
                + table
                + " WHERE "
                + column
                + " >= '2026-08-31T22:00:00Z'"
            )
            assert cursor.fetchone()[0] == 0


def test_metering_simulation_is_unavailable_outside_a_worker(monkeypatch):
    """The normal application cannot install private metering controls."""
    from flask import Flask

    import sandbox_metering

    monkeypatch.delenv("OPENLEG_SANDBOX_WORKER", raising=False)
    web = Flask(__name__)
    with pytest.raises(RuntimeError, match="requires a village worker"):
        sandbox_metering.install(web)
    assert not any(
        rule.rule.startswith("/sandbox/") for rule in web.url_map.iter_rules()
    )
