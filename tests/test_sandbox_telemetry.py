# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local fictional samples use ordinary telemetry and consent contracts."""

import html
import json
import re
import stat
import time

import pytest

from tests import test_sandbox_http
from tests.test_sandbox_metering import _workspace_connection
from tests.test_sandbox_payments import _issued
from tests.test_sandbox_personas import _start, _switch

village = test_sandbox_http.village
_csrf = test_sandbox_http._csrf
pytestmark = pytest.mark.integration
COMMUNITY = "sonnwil-operating-leg"


def _enroll(client, prefix):
    path = prefix + "/sandbox/telemetry"
    page = client.get(path)
    enrolled = client.post(
        path + "/enroll",
        data={"csrf_token": _csrf(page), "confirm": "yes"},
        follow_redirects=True,
    )
    assert enrolled.status_code == 200
    installation = re.search(
        r'<article id="installation-([a-f0-9-]+)"', enrolled.text
    ).group(1)
    credential = re.search(r'id="issued-credential"[^>]*>([^<]+)', enrolled.text).group(
        1
    )
    assert enrolled.headers["Cache-Control"] == "no-store"
    assert credential not in client.get(path).text
    return installation, credential


def _control(client, prefix, installation, action, **fields):
    page = client.get(prefix + "/sandbox/telemetry")
    return client.post(
        prefix + f"/sandbox/telemetry/{installation}/{action}",
        data={"csrf_token": _csrf(page), "confirm": "yes", **fields},
        follow_redirects=True,
    )


def _result(response):
    return json.loads(
        html.unescape(
            re.search(
                r'id="source-result"[^>]*>(.*?)</pre>', response.text, re.DOTALL
            ).group(1)
        )
    )


def test_local_source_uses_real_freshness_faults_credentials_and_billing_isolation(
    village,
):
    """Local samples retain normal freshness, fault and credential behavior without billing writes."""
    client = village.test_client()
    prefix, _ = _issued(village, client)
    _switch(client, prefix, "resident")
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute("SELECT row_to_json(i) FROM invoices i ORDER BY id")
        invoices_before = cur.fetchall()
        cur.execute("SELECT row_to_json(p) FROM billing_periods p ORDER BY id")
        periods_before = cur.fetchall()
        cur.execute("SELECT COUNT(*) FROM meter_readings")
        readings_before = cur.fetchone()
    equipment = client.post(
        prefix + "/api/equipment/v1/records",
        json={"manufacturer": "SIMULATION", "model": "SONNWIL"},
        headers={"X-CSRF-Token": _csrf(client.get(prefix + "/dashboard"))},
    )
    assert equipment.status_code == 201
    installation, original = _enroll(client, prefix)
    api = (
        prefix
        + f"/api/telemetry/v1/communities/{COMMUNITY}/installations/{installation}"
    )
    published = _control(client, prefix, installation, "publish")
    assert published.status_code == 200 and _result(published)["accepted"] == 2
    assert _result(_control(client, prefix, installation, "replay"))["duplicates"] == 2
    samples = client.get(api + "/samples").json["samples"]
    assert len(samples) == 2 and all(row["freshness"] == "fresh" for row in samples)
    assert "Aktuell" in client.get(prefix + "/dashboard/telemetry").text
    assert _control(client, prefix, installation, "pause").status_code == 200
    assert _control(client, prefix, installation, "publish").status_code == 409
    deadline = time.monotonic() + 70
    while time.monotonic() < deadline:
        samples = client.get(api + "/samples").json["samples"]
        if all(row["freshness"] == "stale" for row in samples):
            break
        time.sleep(1)
    assert all(row["freshness"] == "stale" for row in samples)
    assert "Veraltet" in client.get(prefix + "/dashboard/telemetry").text
    assert _control(client, prefix, installation, "resume").status_code == 200
    assert _control(client, prefix, installation, "fault").status_code == 200
    page = client.get(prefix + "/dashboard/telemetry")
    assert "Gerät meldet eine Störung" in page.text
    notices = client.get(api + "/notices").json["notices"]
    fault = next(row for row in notices if row["kind"] == "device_fault")
    ack = client.post(
        prefix
        + f"/dashboard/telemetry/{installation}/notices/{fault['id']}/acknowledge",
        data={
            "csrf_token": _csrf(page),
            "community_id": COMMUNITY,
            "episode": fault["episode"],
        },
    )
    assert ack.status_code == 303
    assert _control(client, prefix, installation, "clear").status_code == 200
    assert (
        "Gerät meldet keine Störung mehr"
        in client.get(prefix + "/dashboard/telemetry").text
    )
    rotated = _control(client, prefix, installation, "rotate")
    assert rotated.status_code == 200
    current = re.search(r'id="issued-credential"[^>]*>([^<]+)', rotated.text).group(1)
    assert current != original
    assert (
        _control(client, prefix, installation, "test-old-credential").status_code == 401
    )
    assert (
        client.post(
            api + "/samples", json={}, headers={"Authorization": "Bearer " + original}
        ).status_code
        == 401
    )
    assert _control(client, prefix, installation, "publish").status_code == 200
    assert _control(client, prefix, installation, "revoke").status_code == 200
    assert _control(client, prefix, installation, "publish").status_code == 401
    assert (
        client.post(
            api + "/samples", json={}, headers={"Authorization": "Bearer " + current}
        ).status_code
        == 401
    )
    workspace = village.extensions["villages"].workspaces[prefix.rsplit("/", 1)[1]]
    source = workspace["directory"] / "simulation-telemetry" / (installation + ".json")
    assert stat.S_IMODE(source.stat().st_mode) == 0o600
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute("SELECT row_to_json(i) FROM invoices i ORDER BY id")
        assert cur.fetchall() == invoices_before
        cur.execute("SELECT row_to_json(p) FROM billing_periods p ORDER BY id")
        assert cur.fetchall() == periods_before
        cur.execute("SELECT COUNT(*) FROM meter_readings")
        assert cur.fetchone() == readings_before


def test_source_and_shares_recheck_owner_consent_and_workspace(village, monkeypatch):
    """Source controls and shares recheck ownership, consent and visitor scope."""
    client = village.test_client()
    prefix, _ = _start(client, "resident")
    path = prefix + "/sandbox/telemetry"
    page = client.get(path)
    assert client.post(path + "/enroll", data={"confirm": "yes"}).status_code == 400
    assert (
        client.post(path + "/enroll", data={"csrf_token": _csrf(page)}).status_code
        == 400
    )
    installation, credential = _enroll(client, prefix)
    api = (
        prefix
        + f"/api/telemetry/v1/communities/{COMMUNITY}/installations/{installation}"
    )
    summary = (
        prefix + f"/api/telemetry/v1/communities/{COMMUNITY}/installations/summary"
    )
    assert _control(client, prefix, installation, "publish").status_code == 200
    _switch(client, prefix, "producer")
    assert installation not in client.get(path).text
    assert _control(client, prefix, installation, "publish").status_code == 404
    assert _control(client, prefix, installation, "rotate").status_code == 404
    assert client.get(api + "/samples").status_code == 403
    assert client.get(summary).json["sources"] == []
    _switch(client, prefix, "resident")
    assert (
        _control(
            client, prefix, installation, "share", viewer="sonnwil-producer"
        ).status_code
        == 200
    )
    _switch(client, prefix, "producer")
    shared = client.get(summary)
    assert shared.status_code == 200 and installation in shared.text
    assert (
        credential not in shared.text
        and "SIMULATION SONNWIL local samples" not in shared.text
    )
    assert client.get(api + "/notices").status_code == 403
    _switch(client, prefix, "resident")
    settings = client.get(prefix + "/dashboard")
    assert (
        client.post(
            prefix + "/dashboard/profile",
            data={
                "csrf_token": _csrf(settings),
                "annual_consumption_kwh": "4500",
                "potential_pv_kwp": "0",
            },
        ).status_code
        == 302
    )
    _switch(client, prefix, "producer")
    assert installation not in client.get(summary).text
    _switch(client, prefix, "resident")
    settings = client.get(prefix + "/dashboard")
    assert (
        client.post(
            prefix + "/dashboard/profile",
            data={
                "csrf_token": _csrf(settings),
                "annual_consumption_kwh": "4500",
                "potential_pv_kwp": "0",
                "share_with_neighbors": "on",
            },
        ).status_code
        == 302
    )
    _switch(client, prefix, "producer")
    assert installation in client.get(summary).text
    _switch(client, prefix, "resident")
    assert (
        _control(
            client, prefix, installation, "unshare", viewer="sonnwil-producer"
        ).status_code
        == 200
    )
    _switch(client, prefix, "producer")
    assert client.get(api + "/samples").status_code == 403
    assert installation not in client.get(summary).text
    other = village.test_client()
    other_prefix, _ = _start(other, "resident")
    assert other.get(path).status_code == 410
    assert (
        other.post(
            other_prefix + f"/sandbox/telemetry/{installation}/publish",
            data={
                "csrf_token": _csrf(other.get(other_prefix + "/sandbox/telemetry")),
                "confirm": "yes",
            },
        ).status_code
        == 404
    )
    from flask import Flask

    import sandbox_telemetry

    monkeypatch.delenv("OPENLEG_SANDBOX_WORKER", raising=False)
    with pytest.raises(RuntimeError, match="requires a village worker"):
        sandbox_telemetry.install(Flask(__name__))


def test_concurrent_local_publish_uses_distinct_monotonic_sequences(village):
    """Concurrent local publications allocate distinct monotonic sequences."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    client = village.test_client()
    prefix, _ = _start(client, "resident")
    installation, _ = _enroll(client, prefix)
    page = client.get(prefix + "/sandbox/telemetry")
    fields = {"csrf_token": _csrf(page), "confirm": "yes"}
    path = prefix + f"/sandbox/telemetry/{installation}/publish"
    assert client.post(path, data={"confirm": "yes"}).status_code == 400
    assert client.post(path, data={"csrf_token": _csrf(page)}).status_code == 400
    clients = [village.test_client(), village.test_client()]
    for clone in clients:
        for cookie in client._cookies.values():
            clone.set_cookie(
                cookie.key, cookie.value, domain=cookie.domain, path=cookie.path
            )
    barrier = Barrier(2)

    def publish(clone):
        barrier.wait(timeout=10)
        return clone.post(path, data=fields, follow_redirects=True).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(publish, clients)) == [200, 200]
    api = (
        prefix
        + f"/api/telemetry/v1/communities/{COMMUNITY}/installations/{installation}/samples"
    )
    samples = client.get(api).json["samples"]
    assert [row["sequence"] for row in samples] == [1, 2, 3, 4]
    assert len({row["sample_id"] for row in samples}) == 4


def test_enrollment_redirect_consumes_private_key_once_without_refresh_replay(village):
    from flask.sessions import session_json_serializer
    from itsdangerous import URLSafeTimedSerializer

    client = village.test_client()
    prefix, _ = _start(client, "resident")
    path = prefix + "/sandbox/telemetry"
    response = client.post(
        path + "/enroll", data={"csrf_token": _csrf(client.get(path)), "confirm": "yes"}
    )
    assert response.status_code == 303
    receipt_cookie = client.get_cookie("openleg_product", path=prefix)
    encoded = receipt_cookie.value.rsplit(".", 2)[0]
    payload = URLSafeTimedSerializer(
        "unused", serializer=session_json_serializer
    ).load_payload(encoded.encode())
    assert "olt_" not in json.dumps(payload)
    shown = client.get(response.location)
    credential = re.search(r'id="issued-credential"[^>]*>([^<]+)', shown.text).group(1)
    assert credential not in client.get(path).text
    client.set_cookie("openleg_product", receipt_cookie.value, path=prefix)
    assert credential not in client.get(path).text
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute("SELECT COUNT(*) FROM telemetry_installations")
        assert cur.fetchone()[0] == 1


@pytest.mark.parametrize(
    ("language", "paused_label", "unpaused_label", "old_ready_label"),
    [
        (
            "de-CH",
            "Quelle pausiert",
            "Quelle nicht pausiert",
            "Quelle bereit für einzelne Muster",
        ),
        (
            "fr-CH",
            "Source en pause",
            "Source non mise en pause",
            "Source prête pour des échantillons manuels",
        ),
    ],
)
def test_revoked_source_status_describes_pause_without_promising_delivery(
    village, language, paused_label, unpaused_label, old_ready_label
):
    """A revoked source remains unpaused without claiming it can publish samples."""
    client = village.test_client()
    prefix, _ = _start(client, "resident")
    installation, _ = _enroll(client, prefix)
    assert _control(client, prefix, installation, "pause").status_code == 200
    path = prefix + "/sandbox/telemetry?lang=" + language
    assert paused_label in client.get(path).text
    assert _control(client, prefix, installation, "resume").status_code == 200
    assert _control(client, prefix, installation, "revoke").status_code == 200
    page = client.get(path)
    assert unpaused_label in page.text
    assert old_ready_label not in page.text
    assert _control(client, prefix, installation, "publish").status_code == 401
