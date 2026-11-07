# SPDX-License-Identifier: AGPL-3.0-or-later
"""A visitor restores a real archive into an isolated disposable destination."""

import html
import io
import json
import re
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from psycopg2 import sql

from tests import test_sandbox_http
from tests.test_sandbox_metering import _workspace_connection
from tests.test_sandbox_payments import _issued
from tests.test_sandbox_personas import _reload_worker, _start, _switch

village = test_sandbox_http.village
pytestmark = pytest.mark.integration
COMMUNITY = "sonnwil-operating-leg"
_csrf = test_sandbox_http._csrf


def _command(client, prefix, action, **changes):
    page = client.get(prefix + "/sandbox/archive")
    assert page.status_code == 200
    return client.post(
        prefix + "/sandbox/archive/" + action,
        data={
            "csrf_token": _csrf(page),
            "community_id": COMMUNITY,
            **changes,
        },
    )


def _result(client, prefix):
    page = client.get(prefix + "/sandbox/archive")
    match = re.search(
        r'<pre id="archive-result"[^>]*>(.*?)</pre>', page.text, re.DOTALL
    )
    assert match
    return json.loads(html.unescape(match[1]))


def _destination(village, prefix):
    parent = village.extensions["villages"].workspaces[prefix.rsplit("/", 1)[1]]
    return "/v/" + parent["archive"]["destination"]


def test_administrator_can_open_the_self_guided_archive_journey(village):
    client = village.test_client()
    prefix, _ = _start(client, "administrator")
    page = client.get(prefix + "/sandbox/archive")
    assert page.status_code == 200
    assert "sonnwil-operating-leg" in page.text
    assert "archive/prepare" in page.text


def test_parent_retirement_during_child_provisioning_keeps_proofs_and_cleans_both(
    village, monkeypatch
):
    """Retiring a source during child provisioning preserves proofs and cleans both workers."""
    client = village.test_client()
    prefix, _ = _start(client, "administrator")
    manager = village.extensions["villages"]
    source_id = prefix.rsplit("/", 1)[1]
    entered, release = Event(), Event()
    provision = manager._provision

    def pause_child(workspace, *args):
        """Hold child provisioning while the source retirement races it."""
        if workspace.get("restore_parent"):
            entered.set()
            assert release.wait(timeout=20)
        return provision(workspace, *args)

    monkeypatch.setattr(manager, "_provision", pause_child)
    with ThreadPoolExecutor(max_workers=2) as executor:
        preparing = executor.submit(_command, client, prefix, "prepare")
        try:
            assert entered.wait(timeout=10)
            retiring = executor.submit(manager.discard, source_id)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with manager.lock:
                    if source_id in manager.retiring:
                        break
                time.sleep(0.01)
            else:
                pytest.fail("parent retirement did not claim the source")
            assert not retiring.done()
        finally:
            release.set()
        assert preparing.result(timeout=40).status_code == 410
        assert retiring.result(timeout=40) is True
    assert not manager.workspaces and not manager.retiring
    assert client.get(prefix + "/dashboard/export").status_code == 410


def test_empty_restore_destination_with_lost_fixture_never_seeds_on_reload(village):
    client = village.test_client()
    prefix, _ = _start(client, "administrator")
    assert _command(client, prefix, "prepare").status_code == 303
    destination = _destination(village, prefix)
    manager = village.extensions["villages"]
    child = manager.workspaces[destination.rsplit("/", 1)[1]]
    manifest = child["directory"] / "fixture.json"
    manifest.unlink()
    child["process"].send_signal(signal.SIGHUP)
    assert child["process"].wait(timeout=20) != 0
    assert not manifest.exists()
    with (
        _workspace_connection(village, destination) as connection,
        connection.cursor() as cur,
    ):
        cur.execute("SELECT COUNT(*) FROM buildings")
        assert cur.fetchone() == (0,)
    assert client.get(prefix + "/sandbox/archive").status_code == 200
    assert _command(client, prefix, "discard", confirm="discard").status_code == 303
    assert client.get(destination + "/dashboard/export").status_code == 410


def test_real_archive_round_trip_rollback_replay_conflict_and_private_discard(village):
    """Real archive commands preserve rollback, replay, conflict and private discard behavior."""
    village.extensions["villages"].max_workspaces = 4
    client = village.test_client()
    prefix, invoices = _issued(village, client)
    export_path = "/leg/community/" + COMMUNITY + "/archive"
    exported = client.get(prefix + export_path)
    assert exported.status_code == 200
    assert "SIMULATION SONNWIL" in exported.json["manifest"]["simulation"]
    assert "simulation-sonnwil" in exported.headers["Content-Disposition"]
    assert _command(client, prefix, "prepare").status_code == 303
    assert _result(client, prefix) == {"valid": True, "errors": [], "conflicts": []}
    destination = _destination(village, prefix)
    manager = village.extensions["villages"]
    original = (
        manager.workspaces[prefix.rsplit("/", 1)[1]]["directory"] / "fixture.json"
    )
    copied = (
        manager.workspaces[destination.rsplit("/", 1)[1]]["directory"] / "fixture.json"
    )
    assert copied.read_bytes() == original.read_bytes()
    assert copied.stat().st_mode & 0o777 == 0o600
    assert client.get(destination + "/dashboard/export").status_code == 401
    with (
        _workspace_connection(village, destination) as connection,
        connection.cursor() as cur,
    ):
        cur.execute("SELECT COUNT(*) FROM buildings")
        assert cur.fetchone()[0] == 0
        cur.execute("""CREATE FUNCTION sandbox_fail_invoice() RETURNS trigger LANGUAGE plpgsql AS $$
                       BEGIN RAISE EXCEPTION 'synthetic late restore failure'; END $$""")
        cur.execute(
            "CREATE TRIGGER sandbox_fail_invoice BEFORE INSERT ON invoices FOR EACH ROW EXECUTE FUNCTION sandbox_fail_invoice()"
        )
    assert _command(client, prefix, "restore", confirm="restore").status_code == 503
    failed_guide = client.get(prefix + "/sandbox/archive")
    assert failed_guide.status_code == 200
    assert 'id="archive-result"' not in failed_guide.text
    assert "Vorprüfung erfolgreich" not in failed_guide.text
    with (
        _workspace_connection(village, destination) as connection,
        connection.cursor() as cur,
    ):
        for table in ("buildings", "communities", "billing_periods", "invoices"):
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            assert cur.fetchone()[0] == 0
        cur.execute("DROP TRIGGER sandbox_fail_invoice ON invoices")
        cur.execute("DROP FUNCTION sandbox_fail_invoice()")
    assert _command(client, prefix, "restore", confirm="restore").status_code == 303
    assert _result(client, prefix)["restored"] is True
    opened = _command(client, prefix, "open")
    assert opened.status_code == 303 and opened.location.startswith(destination)
    product = client.get(opened.location, follow_redirects=True)
    assert product.status_code == 200 and "LEG Sonnwil" in product.text
    _reload_worker(village, destination)
    assert "Lea Muster" in _switch(client, destination, "resident").text
    _switch(client, destination, "administrator")
    assert (
        client.get(
            destination + "/sandbox/archive/enter",
            headers={
                "X-OpenLEG-Restore-Actor": "sonnwil-neighbour",
                "X-OpenLEG-Restore-Community": COMMUNITY,
            },
        ).status_code
        == 403
    )
    chooser = client.get(destination + "/sandbox/accounts")
    assert (
        'value="municipality"' not in chooser.text
        and 'value="utility"' not in chooser.text
    )
    for unavailable in ("municipality", "utility"):
        assert (
            client.post(
                destination + "/sandbox/persona",
                data={"csrf_token": _csrf(chooser), "persona": unavailable},
            ).status_code
            == 410
        )
        assert client.get(destination + "/dashboard/export").status_code == 200
    restored = client.get(destination + export_path)
    assert restored.status_code == 200
    assert destination + export_path in client.get(destination + "/sandbox/manage").text
    assert restored.json["datasets"] == exported.json["datasets"]
    invoice = invoices["sonnwil-neighbour"]
    for location in (prefix, destination):
        invoice_page = client.get(location + f"/dashboard/invoices/{invoice}")
        assert invoice_page.status_code == 200 and "43.20" in invoice_page.text
        assert client.get(location + "/sandbox/payments").status_code == 200
        assert client.get(location + "/sandbox/metering").status_code == 200
    assert _command(client, prefix, "restore", confirm="restore").status_code == 303
    assert _result(client, prefix)["restored"] is True
    assert (
        client.get(destination + export_path).json["datasets"]
        == restored.json["datasets"]
    )
    profile = client.get(destination + "/dashboard")
    assert (
        client.post(
            destination + "/dashboard/profile",
            data={
                "csrf_token": _csrf(profile),
                "annual_consumption_kwh": "9900",
                "potential_pv_kwp": "0",
            },
        ).status_code
        == 302
    )
    changed = client.get(destination + export_path).json["datasets"]
    assert _command(client, prefix, "restore", confirm="restore").status_code == 303
    conflict = _result(client, prefix)
    assert conflict["valid"] and conflict["conflicts"] and not conflict.get("restored")
    assert client.get(destination + export_path).json["datasets"] == changed
    assert (
        client.get(prefix + export_path).json["datasets"] == exported.json["datasets"]
    )
    assert _command(client, prefix, "prepare", variant="damaged").status_code == 303
    assert not _result(client, prefix)["valid"]
    assert _command(client, prefix, "restore", confirm="restore").status_code == 303
    assert not _result(client, prefix)["valid"]
    assert client.get(destination + export_path).json["datasets"] == changed
    other = village.test_client()
    other_prefix, _ = _start(other, "administrator")
    manager = village.extensions["villages"]
    target = manager.workspaces[destination.rsplit("/", 1)[1]]
    activity = target["last_active"]
    assert other.get(destination + export_path).status_code == 410
    assert target["last_active"] == activity
    assert (
        _command(other, other_prefix, "restore", confirm="restore").status_code == 400
    )
    assert _command(client, prefix, "discard", confirm="discard").status_code == 303
    assert client.get(destination + "/dashboard").status_code == 410
    assert client.get(prefix + "/dashboard/export").status_code == 200
    assert other.get(other_prefix + "/dashboard/export").status_code == 200


def test_failed_preflight_hides_old_result_and_preserves_restored_destination(village):
    """An unavailable destination hides stale results without losing restored access."""
    client = village.test_client()
    prefix, _ = _start(client, "administrator")
    assert _command(client, prefix, "prepare").status_code == 303
    assert _command(client, prefix, "restore", confirm="restore").status_code == 303
    assert _result(client, prefix)["restored"] is True
    destination = _destination(village, prefix)
    target = village.extensions["villages"].workspaces[destination.rsplit("/", 1)[1]]
    with _workspace_connection(village, prefix) as connection:
        with connection.cursor() as cur:
            name = sql.Identifier(target["name"])
            cur.execute(
                sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS false").format(name)
            )
            connection.commit()
            try:
                assert _command(client, prefix, "prepare").status_code == 503
                guide = client.get(prefix + "/sandbox/archive")
                assert guide.status_code == 200
                assert 'id="archive-result"' not in guide.text
                assert (
                    "Der gespeicherte Stand ist im privaten Ziel wiederhergestellt."
                    not in guide.text
                )
                assert prefix + "/sandbox/archive/open" in guide.text
            finally:
                cur.execute(
                    sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS true").format(name)
                )
                connection.commit()
    opened = _command(client, prefix, "open")
    assert opened.status_code == 303 and opened.location.startswith(destination)
    assert client.get(opened.location, follow_redirects=True).status_code == 200


@pytest.mark.parametrize("version", [[], {}], ids=["list", "dictionary"])
def test_uploaded_archive_rejects_nonstring_versions_without_mutation(village, version):
    """Malformed version uploads return validation errors and preserve source records."""
    client = village.test_client()
    prefix, _ = _start(client, "administrator")
    path = prefix + "/leg/community/" + COMMUNITY + "/archive"
    exported = client.get(path)
    assert exported.status_code == 200
    payload = exported.json
    payload["manifest"]["schema_version"] = version
    guide = client.get(prefix + "/sandbox/archive")
    response = client.post(
        path + "/dry-run",
        data={
            "csrf_token": _csrf(guide),
            "archive": (io.BytesIO(json.dumps(payload).encode()), "malformed.json"),
        },
    )
    assert response.status_code == 400
    assert response.json == {
        "valid": False,
        "errors": ["Unsupported schema version"],
        "conflicts": [],
    }
    assert client.get(path).json["datasets"] == exported.json["datasets"]


def test_archive_commands_recheck_role_csrf_and_do_not_grant_platform_authority(
    village,
):
    """Archive controls recheck role and CSRF without granting platform authority."""
    client = village.test_client()
    prefix, _ = _start(client, "administrator")
    path = prefix + "/sandbox/archive"
    assert (
        client.post(path + "/prepare", data={"community_id": COMMUNITY}).status_code
        == 400
    )
    assert (
        _command(
            client, prefix, "prepare", community_id="sonnwil-private-leg"
        ).status_code
        == 403
    )
    assert _command(client, prefix, "prepare").status_code == 303
    destination = _destination(village, prefix)
    assert (
        client.get(
            destination + "/sandbox/archive/enter",
            headers={
                "X-OpenLEG-Restore-Actor": "sonnwil-neighbour",
                "X-OpenLEG-Restore-Community": COMMUNITY,
            },
        ).status_code
        == 403
    )
    _switch(client, prefix, "operator")
    assert client.get(path).status_code == 403
    profile = client.get(prefix + "/dashboard")
    for action in ("prepare", "restore", "open", "discard"):
        assert (
            client.post(
                path + "/" + action,
                data={
                    "csrf_token": _csrf(profile),
                    "community_id": COMMUNITY,
                    "confirm": "restore",
                },
            ).status_code
            == 403
        )


@pytest.mark.parametrize("retirement", ["reset", "discard", "expiry"])
def test_source_retirement_removes_its_destination_and_leaves_another_visitor(
    village, retirement
):
    """Source retirement cleans its destination while another visitor remains usable."""
    manager = village.extensions["villages"]
    manager.max_workspaces = 4
    client, other = village.test_client(), village.test_client()
    prefix, _ = _start(client, "administrator")
    assert _command(client, prefix, "prepare").status_code == 303
    destination = _destination(village, prefix)
    source_id, destination_id = (p.rsplit("/", 1)[1] for p in (prefix, destination))
    directories = [
        manager.workspaces[i]["directory"] for i in (source_id, destination_id)
    ]
    other_prefix, _ = _start(other, "resident")
    if retirement == "expiry":
        manager.workspaces[source_id]["created"] = (
            time.monotonic() - manager.lifetime_seconds - 1
        )
        assert client.get(prefix + "/sandbox/home").status_code == 410
    else:
        manage = client.get(prefix + "/sandbox/manage")
        from sandbox_seed import SEED_VERSION

        response = client.post(
            prefix + "/sandbox/" + retirement,
            data={
                "csrf_token": _csrf(manage),
                "confirm": retirement,
                "seed_version": SEED_VERSION,
            },
        )
        assert response.status_code in (302, 303)
        if retirement == "reset":
            assert (
                client.get(response.location, follow_redirects=True).status_code == 200
            )
    assert client.get(destination + "/dashboard").status_code == 410
    assert all(
        i not in manager.workspaces and i not in manager.retiring
        for i in (source_id, destination_id)
    )
    assert all(not path.exists() for path in directories)
    assert other.get(other_prefix + "/dashboard/export").status_code == 200


def test_source_expiring_during_export_cannot_allocate_an_orphan_destination(
    village, monkeypatch
):
    """Expiry during export refuses allocation of an orphan destination."""
    manager = village.extensions["villages"]
    client = village.test_client()
    prefix, _ = _start(client, "administrator")
    source_id = prefix.rsplit("/", 1)[1]
    create = manager.create

    def expire_before_allocation(*args, **kwargs):
        with manager.lock:
            manager.workspaces[source_id]["created"] = (
                time.monotonic() - manager.lifetime_seconds - 1
            )
            return create(*args, **kwargs)

    monkeypatch.setattr(manager, "create", expire_before_allocation)
    assert _command(client, prefix, "prepare").status_code == 410
    manager.reap()
    assert not manager.workspaces and not manager.retiring


def test_signed_formation_receipts_and_original_documents_survive_real_restore(village):
    """Real restore preserves original signed documents and their local receipts."""
    from tests.test_sandbox_formation import _create_ready

    client = village.test_client()
    prefix, cid = _create_ready(client)
    guide = prefix + "/sandbox/formation?cid=" + cid
    for role in ("founder", "resident", "producer"):
        _switch(client, prefix, role)
        page = client.get(guide)
        bundle = re.search(r'name="bundle" value="([a-f0-9]{64})"', page.text)
        assert bundle
        assert (
            client.post(
                prefix + f"/sandbox/formation/{cid}/signatures",
                data={"csrf_token": _csrf(page), "bundle": bundle[1], "confirm": "yes"},
            ).status_code
            == 302
        )
    _switch(client, prefix, "founder")
    path = f"/leg/community/{cid}/archive"
    original = client.get(prefix + path).json
    assert len(original["datasets"]["leg_signature_receipts"]) == 3
    assert all(
        row["participant_manifest"] for row in original["datasets"]["leg_documents"]
    )
    assert _command(client, prefix, "prepare", community_id=cid).status_code == 303
    assert _result(client, prefix)["valid"]
    destination = _destination(village, prefix)
    assert (
        _command(
            client, prefix, "restore", community_id=cid, confirm="restore"
        ).status_code
        == 303
    )
    opened = _command(client, prefix, "open", community_id=cid)
    assert client.get(opened.location, follow_redirects=True).status_code == 200
    restored = client.get(destination + path).json
    assert restored["datasets"] == original["datasets"]
    page = client.get(destination + "/sandbox/formation?cid=" + cid)
    assert (
        client.post(
            destination + f"/leg/community/{cid}/vnb-submissions",
            data={"csrf_token": _csrf(page)},
        ).status_code
        == 302
    )
    with (
        _workspace_connection(village, destination) as connection,
        connection.cursor() as cur,
    ):
        cur.execute(
            "SELECT state FROM vnb_submission_cases WHERE community_id=%s", (cid,)
        )
        assert cur.fetchone() == ("delivered",)
    assert client.get(prefix + path).json["datasets"] == original["datasets"]
