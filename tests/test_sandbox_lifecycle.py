# SPDX-License-Identifier: AGPL-3.0-or-later
"""Visitor-owned reset, discard and automatic retirement over real HTTP."""

import re

import pytest

from tests import test_sandbox_http

village = test_sandbox_http.village
_csrf = test_sandbox_http._csrf


def _start(client):
    page = client.get("/sandbox")
    response = client.post(
        "/sandbox/start", data={"csrf_token": _csrf(page), "persona": "resident"}
    )
    assert response.status_code == 302
    mount = re.fullmatch(r"(/v/[a-f0-9]{32})/sandbox/enter", response.location)
    assert mount, "entry must bind every tab to its workspace generation"
    prefix = mount.group(1)
    page = client.get(response.location, follow_redirects=True)
    assert page.status_code == 200
    return prefix, page


def test_reset_requires_confirmation_retires_old_tabs_and_preserves_other_visitor(
    village,
):
    first, second = village.test_client(), village.test_client()
    old, page = _start(first)
    other, other_page = _start(second)
    for client, prefix, profile, value in (
        (first, old, page, "7200"),
        (second, other, other_page, "5800"),
    ):
        assert (
            client.post(
                prefix + "/dashboard/profile",
                data={
                    "csrf_token": _csrf(profile),
                    "annual_consumption_kwh": value,
                    "potential_pv_kwp": "8",
                    "share_with_neighbors": "on",
                },
            ).status_code
            == 302
        )
    import hashlib

    import psycopg2
    from psycopg2.extensions import make_dsn

    manager = village.extensions["villages"]
    workspace = manager.workspaces[old.rsplit("/", 1)[1]]
    for name in ("uploads", "documents", "webhooks", "cache", "restore-scratch"):
        artifact = workspace["directory"] / name
        artifact.mkdir()
        (artifact / "fictional.txt").write_text("synthetic artifact")
    with (
        psycopg2.connect(
            make_dsn(manager.postgres.dsn, dbname=workspace["name"])
        ) as conn,
        conn.cursor() as cursor,
    ):
        cursor.execute(
            "INSERT INTO communities(community_id,name) VALUES ('fictional-leg','Fictional LEG')"
        )
        cursor.execute(
            """INSERT INTO operator_api_clients(id,community_id,created_by,name,capabilities,token_hash)
               VALUES ('fictional-api','fictional-leg','sonnwil-resident','Fictional API',
                       '["payments.read"]',%s)""",
            (hashlib.sha256(b"fictional-api-token").hexdigest(),),
        )
    api = "/api/operator/v1/communities/fictional-leg/payments/matches"
    headers = {"Authorization": "Bearer fictional-api-token"}
    assert first.get(old + api, headers=headers).status_code == 200
    assert (
        first.post(
            old + "/dashboard/access/request",
            data={"email": "sonnwil-resident@example.org"},
        ).status_code
        == 200
    )
    inbox = first.get(old + "/sandbox/inbox")
    link = re.search(r'href="(' + old + r'/dashboard/access/[^"?]+)', inbox.text).group(
        1
    )
    confirmation = first.get(old + "/sandbox/manage")
    assert confirmation.status_code == 200
    data = {"csrf_token": _csrf(confirmation), "seed_version": "sonnwil/1"}
    assert first.post(old + "/sandbox/reset", data=data).status_code == 400
    assert first.get(old + "/dashboard/export").json["annual_consumption_kwh"] == 7200
    data["confirm"] = "reset"
    reset = first.post(old + "/sandbox/reset", data=data)
    assert reset.status_code == 302
    assert reset.location != old + "/sandbox/enter"
    replacement = reset.location.removesuffix("/sandbox/enter")
    assert first.get(reset.location, follow_redirects=True).status_code == 200
    assert (
        first.get(replacement + "/dashboard/export").json["annual_consumption_kwh"]
        == 4500
    )
    assert first.get(old + "/dashboard").status_code == 410
    assert first.get(link).status_code == 410
    assert first.get(link.replace(old, replacement)).location.endswith("access=invalid")
    assert first.get(old + api, headers=headers).status_code == 410
    assert first.get(replacement + api, headers=headers).status_code == 401
    assert not workspace["directory"].exists()
    assert (
        first.post(
            old + "/dashboard/profile",
            data={"csrf_token": _csrf(page), "annual_consumption_kwh": "9900"},
        ).status_code
        == 410
    )
    assert (
        first.get(replacement + "/dashboard/export").json["annual_consumption_kwh"]
        == 4500
    )
    assert (
        second.get(other + "/dashboard/export").json["annual_consumption_kwh"] == 5800
    )
    assert first.post(old + "/sandbox/reset", data=data).status_code == 410


def test_discard_does_not_create_a_replacement_and_can_be_repeated(village):
    client = village.test_client()
    prefix, _ = _start(client)
    confirmation = client.get(prefix + "/sandbox/manage")
    response = client.post(
        prefix + "/sandbox/discard",
        data={"csrf_token": _csrf(confirmation), "confirm": "discard"},
    )
    assert response.status_code == 303
    assert client.get(prefix + "/dashboard").status_code == 410
    assert (
        client.post(
            prefix + "/sandbox/discard",
            data={"csrf_token": _csrf(confirmation), "confirm": "discard"},
        ).status_code
        == 410
    )
    assert len(village.extensions["villages"].workspaces) == 0
    fresh, _ = _start(client)
    assert fresh != prefix


def test_failed_cleanup_is_inactive_recorded_and_retried_without_harming_other_village(
    village, monkeypatch
):
    import json

    import psycopg2

    first, second = village.test_client(), village.test_client()
    old, _ = _start(first)
    other, _ = _start(second)
    manager = village.extensions["villages"]
    identifier = old.rsplit("/", 1)[1]
    workspace = manager.workspaces[identifier]
    confirmation = first.get(old + "/sandbox/manage")
    original = psycopg2.connect

    def unavailable(*args, **kwargs):
        raise psycopg2.OperationalError("deliberate scratch cleanup outage")

    with monkeypatch.context() as patch:
        patch.setattr("sandbox_runtime.psycopg2.connect", unavailable)
        response = first.post(
            old + "/sandbox/discard",
            data={"csrf_token": _csrf(confirmation), "confirm": "discard"},
        )
    assert response.status_code == 503
    assert first.get(old + "/dashboard/export").status_code == 410
    assert second.get(other + "/dashboard/export").status_code == 200
    record = json.loads((manager.directory / ".cleanup.json").read_text())
    assert record == [{"id": identifier, "attempts": 1, "error": "OperationalError"}]
    assert workspace["process"].poll() is not None
    assert identifier not in manager.workspaces
    assert psycopg2.connect is original
    workspace["retry_at"] = 0
    manager.reap()
    assert identifier not in manager.retiring
    assert not workspace["directory"].exists()
    assert manager.destroy(identifier) is True
    assert second.get(other + "/dashboard/export").status_code == 200


def test_cleanup_stops_retrying_after_its_bound(village, monkeypatch):
    import psycopg2

    client = village.test_client()
    prefix, _ = _start(client)
    manager = village.extensions["villages"]
    identifier = prefix.rsplit("/", 1)[1]
    workspace = manager.workspaces[identifier]
    attempts = []

    def unavailable(*args, **kwargs):
        attempts.append(1)
        raise psycopg2.OperationalError("deliberate cleanup outage")

    with monkeypatch.context() as patch:
        patch.setattr("sandbox_runtime.psycopg2.connect", unavailable)
        assert manager.destroy(identifier) is False
        for _ in range(5):
            workspace["retry_at"] = 0
            manager.reap()
        assert len(attempts) == 3
    assert workspace["attempts"] == 3
    assert client.get(prefix + "/dashboard").status_code == 410
    assert identifier not in manager.workspaces


def test_expiry_cleans_idle_village_and_returns_capacity_while_another_stays_active(
    village,
):
    import time

    first, second = village.test_client(), village.test_client()
    old, _ = _start(first)
    other, _ = _start(second)
    manager = village.extensions["villages"]
    old_id = old.rsplit("/", 1)[1]
    workspace = manager.workspaces[old_id]
    # Use the real cleanup thread and monotonic time. Activity keeps only the
    # caller's idle deadline alive; maximum lifetime is independent.
    manager.inactivity_seconds = 1
    for _ in range(8):
        assert second.get(other + "/dashboard/export").status_code == 200
        time.sleep(0.25)
    # Worker termination can consume its five-second grace and force-stop
    # windows before PostgreSQL cleanup finishes.
    deadline = time.monotonic() + 15
    while old_id in manager.retiring and time.monotonic() < deadline:
        second.get(other + "/dashboard/export")
        time.sleep(0.1)
    assert old_id not in manager.workspaces
    assert old_id not in manager.retiring
    assert not workspace["directory"].exists()
    assert first.get(old + "/dashboard").status_code == 410
    manager.inactivity_seconds = 900
    fresh, _ = _start(first)
    assert fresh != old
    assert second.get(other + "/dashboard/export").status_code == 200


def test_reset_waits_for_an_inflight_write_and_drops_only_the_old_generation(village):
    import time
    from concurrent.futures import ThreadPoolExecutor

    import psycopg2
    from psycopg2.extensions import make_dsn

    first, second = village.test_client(), village.test_client()
    old, page = _start(first)
    other, other_page = _start(second)
    assert (
        second.post(
            other + "/dashboard/profile",
            data={
                "csrf_token": _csrf(other_page),
                "annual_consumption_kwh": "5800",
                "potential_pv_kwp": "8",
            },
        ).status_code
        == 302
    )
    resetter = village.test_client()
    resetter.set_cookie("openleg_sandbox", first.get_cookie("openleg_sandbox").value)
    confirmation = resetter.get(old + "/sandbox/manage")
    manager = village.extensions["villages"]
    workspace = manager.workspaces[old.rsplit("/", 1)[1]]
    connection = psycopg2.connect(
        make_dsn(manager.postgres.dsn, dbname=workspace["name"])
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT building_id FROM buildings WHERE building_id='sonnwil-resident' FOR UPDATE"
            )
            with ThreadPoolExecutor(max_workers=2) as executor:
                write = executor.submit(
                    first.post,
                    old + "/dashboard/profile",
                    data={
                        "csrf_token": _csrf(page),
                        "annual_consumption_kwh": "7200",
                        "potential_pv_kwp": "8",
                    },
                )
                try:
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        cursor.execute("SELECT pg_stat_clear_snapshot()")
                        cursor.execute(
                            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock')"
                        )
                        if cursor.fetchone()[0]:
                            break
                        time.sleep(0.02)
                    else:
                        raise AssertionError(
                            "profile write did not wait for its database row"
                        )
                    reset = executor.submit(
                        resetter.post,
                        old + "/sandbox/reset",
                        data={
                            "csrf_token": _csrf(confirmation),
                            "seed_version": "sonnwil/1",
                            "confirm": "reset",
                        },
                    )
                    time.sleep(0.1)
                    assert not reset.done()
                    assert (
                        second.get(other + "/dashboard/export").json[
                            "annual_consumption_kwh"
                        ]
                        == 5800
                    )
                finally:
                    connection.commit()
                assert write.result(timeout=10).status_code == 302
                response = reset.result(timeout=30)
    finally:
        connection.close()
    assert response.status_code == 302
    assert resetter.get(response.location, follow_redirects=True).status_code == 200
    replacement = response.location.removesuffix("/sandbox/enter")
    assert (
        resetter.get(replacement + "/dashboard/export").json["annual_consumption_kwh"]
        == 4500
    )
    assert resetter.get(old + "/dashboard/export").status_code == 410
    assert (
        second.get(other + "/dashboard/export").json["annual_consumption_kwh"] == 5800
    )
    assert workspace["process"].poll() is not None
    assert not workspace["directory"].exists()
    with psycopg2.connect(manager.postgres.dsn) as admin, admin.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM pg_database WHERE datname=%s", (workspace["name"],)
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT COUNT(*) FROM pg_roles WHERE rolname=%s", (workspace["name"],)
        )
        assert cursor.fetchone()[0] == 0


@pytest.mark.parametrize("missing_worker_journal", [False, True])
def test_gateway_restart_reclaims_orphans_and_keeps_old_sessions_expired(
    tmp_path, missing_worker_journal
):
    import http.cookiejar
    import json
    import os
    import signal
    import socket
    import subprocess
    import sys
    import time
    import urllib.error
    import urllib.parse
    import urllib.request
    from pathlib import Path

    import pytest

    from sandbox_lifecycle import ScratchOwner

    binary = os.environ.get(
        "OPENLEG_SANDBOX_POSTGRES_BIN", "/usr/lib/postgresql/16/bin"
    )
    if not (Path(binary) / "initdb").is_file():
        pytest.skip("requires local PostgreSQL 16 binaries")
    root = tmp_path / "crashed-gateway"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    command = [
        sys.executable,
        "scripts/sandbox.py",
        "--directory",
        str(root),
        "--port",
        str(port),
        "--postgres-bin",
        binary,
    ]
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    processes = []
    old_identity = None

    def start():
        process = subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        processes.append(process)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError("sandbox runner exited before readiness")
            try:
                return opener.open(origin + "/sandbox", timeout=1).read().decode()
            except (OSError, urllib.error.URLError):
                time.sleep(0.1)
        raise AssertionError("sandbox runner did not become ready")

    try:
        entry = start()
        data = urllib.parse.urlencode(
            {
                "csrf_token": re.search(
                    r'name="csrf_token" value="([^"]+)"', entry
                ).group(1),
                "persona": "resident",
            }
        ).encode()
        response = opener.open(origin + "/sandbox/start", data=data, timeout=30)
        old_path = urllib.parse.urlsplit(response.url).path
        identifier = re.match(r"/v/([a-f0-9]{32})/", old_path).group(1)
        old_directory = root / identifier
        identity = json.loads((old_directory / "worker.json").read_text())
        old_identity = identity
        if missing_worker_journal:
            (old_directory / "worker.json").unlink()
        assert old_directory.exists()
        os.kill(processes[0].pid, signal.SIGKILL)
        processes[0].wait(timeout=5)
        start()
        assert not old_directory.exists()
        with pytest.raises(urllib.error.HTTPError) as expired:
            opener.open(origin + old_path, timeout=5)
        assert expired.value.code == 410
        try:
            assert ScratchOwner.start_token(identity["pid"]) != identity["start"]
        except FileNotFoundError:
            pass
        entry = opener.open(origin + "/sandbox", timeout=5).read().decode()
        data = urllib.parse.urlencode(
            {
                "csrf_token": re.search(
                    r'name="csrf_token" value="([^"]+)"', entry
                ).group(1),
                "persona": "resident",
            }
        ).encode()
        fresh = opener.open(origin + "/sandbox/start", data=data, timeout=30)
        assert fresh.status == 200
        assert identifier not in fresh.url
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        if old_identity:
            try:
                if (
                    ScratchOwner.start_token(old_identity["pid"])
                    == old_identity["start"]
                ):
                    os.killpg(old_identity["pid"], signal.SIGKILL)
            except (FileNotFoundError, ProcessLookupError):
                pass
        if root.exists():
            owner = ScratchOwner(root, binary)
            owner.close()


def test_active_requests_cannot_extend_maximum_lifetime(village):
    import math
    import time

    client = village.test_client()
    prefix, _ = _start(client)
    manager = village.extensions["villages"]
    workspace = manager.workspaces[prefix.rsplit("/", 1)[1]]
    manager.lifetime_seconds = math.ceil(time.monotonic() - workspace["created"]) + 1
    successes = 0
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(prefix + "/dashboard/export")
        if response.status_code == 410:
            break
        assert response.status_code == 200
        successes += 1
        time.sleep(0.2)
    else:
        raise AssertionError("activity extended the maximum lifetime")
    assert successes > 0
    cleanup_deadline = time.monotonic() + 15
    while workspace["directory"].exists() and time.monotonic() < cleanup_deadline:
        time.sleep(0.05)
    assert workspace["process"].poll() is not None
    assert not workspace["directory"].exists()


def test_a_second_gateway_cannot_recover_a_live_gateways_storage(village):
    import os

    import pytest

    from sandbox_runtime import create_gateway

    client = village.test_client()
    prefix, _ = _start(client)
    manager = village.extensions["villages"]
    with pytest.raises(RuntimeError, match="already owns"):
        create_gateway(
            manager.directory,
            postgres_bin=os.environ.get(
                "OPENLEG_SANDBOX_POSTGRES_BIN", "/usr/lib/postgresql/16/bin"
            ),
        )
    assert client.get(prefix + "/dashboard/export").status_code == 200


def test_journal_io_failure_still_stops_worker_and_allows_bounded_retry(
    village, monkeypatch, caplog
):
    import math

    import psycopg2

    client = village.test_client()
    prefix, _ = _start(client)
    manager = village.extensions["villages"]
    identifier = prefix.rsplit("/", 1)[1]
    workspace = manager.workspaces[identifier]

    def disk_full(*args, **kwargs):
        raise OSError("deliberate journal storage outage")

    def database_down(*args, **kwargs):
        raise psycopg2.OperationalError("deliberate scratch database outage")

    with monkeypatch.context() as patch:
        patch.setattr(manager.owner, "write", disk_full)
        patch.setattr("sandbox_runtime.psycopg2.connect", database_down)
        assert manager.destroy(identifier) is False
    assert workspace["process"].poll() is not None
    assert math.isfinite(workspace["retry_at"])
    assert workspace["attempts"] == 1
    assert client.get(prefix + "/dashboard").status_code == 410
    assert "journal" in caplog.text.lower()
    workspace["retry_at"] = 0
    manager.reap()
    assert identifier not in manager.retiring
    assert not workspace["directory"].exists()


def test_template_literal_fetches_are_scoped_and_unscoped_requests_fail_closed(village):
    client = village.test_client()
    prefix, _ = _start(client)
    page = client.get(prefix + "/leg-kalkulator")
    assert page.status_code == 200
    import json
    import subprocess

    # Exercise the shipped browser adapter rather than infer request behavior
    # from a rewritten string in source code.
    shim = client.get(prefix + "/static/js/sandbox_urls.js")
    assert shim.status_code == 200
    script = """
const vm = require('node:vm');
const data = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const requests = [];
const context = {
  URL, Request, location: {href:'http://localhost'+data.prefix+'/leg-kalkulator', origin:'http://localhost'},
  document: {currentScript:{dataset:{prefix:data.prefix}}, addEventListener(){}},
  fetch(input) { requests.push(input); return Promise.resolve({}); }
};
vm.runInNewContext(data.shim, context);
vm.runInNewContext('const bfs=999999; fetch(`/api/v1/municipalities/${bfs}/tariffs`);', context);
process.stdout.write(JSON.stringify(requests));
"""
    result = subprocess.run(
        ["node", "-e", script],
        input=json.dumps({"prefix": prefix, "shim": shim.text}),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "http://localhost" + prefix + "/api/v1/municipalities/999999/tariffs"
    ]
    assert client.get("/api/v1/municipalities/999999/tariffs").status_code == 410


def test_reset_reserves_its_replacement_capacity(village, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    first, second, third = (
        village.test_client(),
        village.test_client(),
        village.test_client(),
    )
    prefix, _ = _start(first)
    other, _ = _start(second)
    confirmation = first.get(prefix + "/sandbox/manage")
    third_entry = third.get("/sandbox")
    manager = village.extensions["villages"]
    retired, release = Event(), Event()
    original = manager.destroy

    def pause(identifier, **kwargs):
        result = original(identifier, **kwargs)
        if identifier == prefix.rsplit("/", 1)[1]:
            retired.set()
            assert release.wait(timeout=10)
        return result

    monkeypatch.setattr(manager, "destroy", pause)
    with ThreadPoolExecutor(max_workers=1) as executor:
        reset = executor.submit(
            first.post,
            prefix + "/sandbox/reset",
            data={
                "csrf_token": _csrf(confirmation),
                "seed_version": "sonnwil/1",
                "confirm": "reset",
            },
        )
        try:
            assert retired.wait(timeout=10)
            response = third.post(
                "/sandbox/start",
                data={"csrf_token": _csrf(third_entry), "persona": "resident"},
            )
            assert response.status_code == 503
        finally:
            release.set()
        response = reset.result(timeout=30)
    assert response.status_code == 302
    assert first.get(response.location, follow_redirects=True).status_code == 200
    assert second.get(other + "/dashboard/export").status_code == 200


def test_a_stalled_upload_does_not_prevent_expiry(village):
    import io
    import time
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from urllib.parse import urlencode

    client = village.test_client()
    prefix, page = _start(client)
    manager = village.extensions["villages"]
    workspace = manager.workspaces[prefix.rsplit("/", 1)[1]]
    manager.inactivity_seconds = 1
    reading, release = Event(), Event()

    class SlowBody(io.BytesIO):
        def readinto(self, buffer):
            reading.set()
            assert release.wait(timeout=10)
            return super().readinto(buffer)

    body = SlowBody(
        urlencode(
            {"csrf_token": _csrf(page), "annual_consumption_kwh": "9900"}
        ).encode()
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        upload = executor.submit(
            client.post,
            prefix + "/dashboard/profile",
            input_stream=body,
            content_type="application/x-www-form-urlencoded",
        )
        try:
            assert reading.wait(timeout=5)
            deadline = time.monotonic() + 6
            while workspace["directory"].exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            assert workspace["process"].poll() is not None
            assert not workspace["directory"].exists()
        finally:
            release.set()
        assert upload.result(timeout=10).status_code == 410
    assert not workspace["directory"].exists()


def test_recovery_preserves_unrelated_processes_in_a_shared_worker_group(tmp_path):
    import json
    import os
    import signal
    import subprocess
    import sys
    import time

    from sandbox_lifecycle import ScratchOwner

    root = tmp_path / "scratch"
    workspace = root / ("a" * 32)
    workspace.mkdir(parents=True)
    identities = tmp_path / "children.json"
    supervisor = subprocess.Popen(
        [
            sys.executable,
            "-c",
            """
import json, subprocess, sys, time
from pathlib import Path
private = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', 'gunicorn', 'sandbox_worker'], cwd=sys.argv[1])
unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
Path(sys.argv[2]).write_text(json.dumps({'private': private.pid, 'unrelated': unrelated.pid}))
try:
    time.sleep(60)
finally:
    for process in (private, unrelated):
        process.terminate()
        process.wait(timeout=5)
""",
            str(workspace),
            str(identities),
        ],
        cwd=tmp_path,
        start_new_session=True,
    )
    owner = None
    try:
        deadline = time.monotonic() + 5
        while not identities.exists():
            assert time.monotonic() < deadline
            time.sleep(0.02)
        children = json.loads(identities.read_text())
        private_start = ScratchOwner.start_token(children["private"])
        unrelated_start = ScratchOwner.start_token(children["unrelated"])
        owner = ScratchOwner(root, tmp_path)
        assert not workspace.exists()
        assert not ScratchOwner._running(children["private"], private_start)
        assert ScratchOwner._running(children["unrelated"], unrelated_start)
        assert supervisor.poll() is None
    finally:
        if owner:
            owner.close()
        try:
            os.killpg(supervisor.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        supervisor.wait(timeout=5)


def test_cleanup_retry_preserves_an_unrelated_group_after_worker_pid_reuse(
    village, monkeypatch, tmp_path
):
    import subprocess
    import sys

    import psycopg2

    client = village.test_client()
    prefix, _ = _start(client)
    manager = village.extensions["villages"]
    identifier = prefix.rsplit("/", 1)[1]
    workspace = manager.workspaces[identifier]

    def database_down(*args, **kwargs):
        raise psycopg2.OperationalError("synthetic cleanup outage")

    with monkeypatch.context() as scoped:
        scoped.setattr("sandbox_runtime.psycopg2.connect", database_down)
        assert manager.destroy(identifier) is False
    process = workspace["process"]
    assert process.poll() is not None
    original_pid = process.pid
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=tmp_path,
        start_new_session=True,
    )
    try:
        # Reproduce PID reuse deterministically while retaining Popen's cached
        # completed status. Signals still target a real, separate test group.
        process.pid = unrelated.pid
        workspace["retry_at"] = 0
        manager.reap()
        with pytest.raises(subprocess.TimeoutExpired):
            unrelated.wait(timeout=0.3)
        assert identifier not in manager.retiring
        assert not workspace["directory"].exists()
    finally:
        process.pid = original_pid
        if unrelated.poll() is None:
            unrelated.terminate()
        unrelated.wait(timeout=5)


def test_two_simultaneous_resets_each_keep_their_reserved_capacity(
    village, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Event

    first, second = village.test_client(), village.test_client()
    first_prefix, _ = _start(first)
    second_prefix, _ = _start(second)
    first_id, second_id = (
        first_prefix.rsplit("/", 1)[1],
        second_prefix.rsplit("/", 1)[1],
    )
    manager = village.extensions["villages"]
    data = [
        {
            "csrf_token": _csrf(client.get(prefix + "/sandbox/manage")),
            "seed_version": "sonnwil/1",
            "confirm": "reset",
        }
        for client, prefix in ((first, first_prefix), (second, second_prefix))
    ]
    reserved, release_second = Barrier(2), Event()
    destroy, create = manager.destroy, manager.create

    def staged_destroy(identifier, **kwargs):
        if identifier in (first_id, second_id):
            reserved.wait(timeout=10)
            if identifier == second_id:
                assert release_second.wait(timeout=40)
        return destroy(identifier, **kwargs)

    def release_after_first_create(*args, **kwargs):
        try:
            return create(*args, **kwargs)
        finally:
            if kwargs.get("replacement") == first_id:
                release_second.set()

    monkeypatch.setattr(manager, "destroy", staged_destroy)
    monkeypatch.setattr(manager, "create", release_after_first_create)
    with ThreadPoolExecutor(max_workers=2) as executor:
        one = executor.submit(first.post, first_prefix + "/sandbox/reset", data=data[0])
        two = executor.submit(
            second.post, second_prefix + "/sandbox/reset", data=data[1]
        )
        try:
            responses = [one.result(timeout=45), two.result(timeout=45)]
        finally:
            release_second.set()
            monkeypatch.setattr(manager, "destroy", destroy)
            monkeypatch.setattr(manager, "create", create)
    for client, old, response in zip(
        (first, second), (first_prefix, second_prefix), responses
    ):
        assert response.status_code == 302
        new_prefix = response.location.removesuffix("/sandbox/enter")
        assert new_prefix != old
        assert client.get(response.location, follow_redirects=True).status_code == 200
        assert (
            client.get(new_prefix + "/dashboard/export").json["annual_consumption_kwh"]
            == 4500
        )
        assert client.get(old + "/dashboard").status_code == 410
    assert len(manager.workspaces) == 2
    assert not manager.replacements


def _inline_scripts(page):
    from html.parser import HTMLParser

    class Scripts(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.result, self.current = [], None

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if (
                tag == "script"
                and not attrs.get("src")
                and attrs.get("type", "")
                in ("", "text/javascript", "application/javascript")
            ):
                self.current = []

        def handle_data(self, data):
            if self.current is not None:
                self.current.append(data)

        def handle_endtag(self, tag):
            if tag == "script" and self.current is not None:
                self.result.append("".join(self.current))
                self.current = None

    scripts = Scripts()
    scripts.feed(page.text)
    return scripts.result


def test_generation_routing_preserves_municipality_javascript_syntax(village):
    import json
    import subprocess

    client = village.test_client()
    prefix, _ = _start(client)
    page = client.get(prefix + "/gemeinde/onboarding")
    assert page.status_code == 200
    scripts = _inline_scripts(page)
    assert any("searchResults.innerHTML" in script for script in scripts)
    result = subprocess.run(
        [
            "node",
            "-e",
            "for(const source of JSON.parse(require('node:fs').readFileSync(0,'utf8'))) new Function(source);",
        ],
        input=json.dumps(scripts),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_generation_routing_preserves_openapi_server_and_path_fragments(village):
    import json
    import subprocess

    client = village.test_client()
    prefix, _ = _start(client)
    page = client.get(prefix + "/api/v1/docs")
    assert page.status_code == 200
    code = next(script for script in _inline_scripts(page) if "const spec =" in script)
    result = subprocess.run(
        [
            "node",
            "-e",
            "const vm=require('node:vm'); let spec; const context={document:{getElementById(){return {}; }}, initializeApiReference(_root,value){spec=value;}}; context.window=context; vm.runInNewContext(JSON.parse(require('node:fs').readFileSync(0,'utf8')),context); process.stdout.write(JSON.stringify({server:spec.servers[0].url, paths:Object.keys(spec.paths)}));",
        ],
        input=json.dumps(code),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    spec = json.loads(result.stdout)
    assert spec["server"] == "/api/v1"
    assert "/municipalities" in spec["paths"]
    assert not any(path.startswith("/v/") for path in spec["paths"])


def test_worker_stop_waits_for_a_killed_process_to_exit(tmp_path, monkeypatch):
    import os
    import signal
    import subprocess
    import sys
    import time
    from threading import Timer

    import sandbox_lifecycle
    from sandbox_lifecycle import ScratchOwner

    owner = ScratchOwner(tmp_path / "villages", tmp_path)
    directory = owner.directory / ("a" * 32)
    directory.mkdir()
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import pathlib,signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); pathlib.Path('ready').touch(); time.sleep(60)",
        ],
        cwd=directory,
        start_new_session=True,
    )
    delayed = []
    kill = os.killpg

    def asynchronous_kill(group, sig):
        if sig == signal.SIGKILL:
            timer = Timer(0.2, kill, (group, sig))
            delayed.append(timer)
            timer.start()
        else:
            kill(group, sig)

    try:
        deadline = time.monotonic() + 5
        while not (directory / "ready").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (directory / "ready").exists()
        owner.record_worker(directory, process)
        monkeypatch.setattr(sandbox_lifecycle.os, "killpg", asynchronous_kill)
        owner.stop_workspace(directory)
        assert delayed
        assert process.poll() is not None, (
            "cleanup returned before the killed worker exited"
        )
    finally:
        for timer in delayed:
            timer.join(timeout=2)
        if process.poll() is None:
            kill(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        owner.close()


def test_recovery_times_out_stalled_postgres_control_and_keeps_state_for_retry(
    tmp_path, monkeypatch
):
    import os
    import signal
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    import psycopg2

    import sandbox_lifecycle
    from sandbox_lifecycle import ScratchOwner
    from sandbox_runtime import LocalPostgres

    binary = Path(
        os.environ.get("OPENLEG_SANDBOX_POSTGRES_BIN", "/usr/lib/postgresql/16/bin")
    )
    if not (binary / "initdb").is_file():
        pytest.skip("requires local PostgreSQL 16 binaries")
    directory = tmp_path / "villages"
    directory.mkdir(mode=0o700)
    stale = directory / "postgres-012345abcdef"
    fake_bin = tmp_path / "stalled-control"
    fake_bin.mkdir()
    command = fake_bin / "pg_ctl"
    command.write_text(f"#!{sys.executable}\nimport time; time.sleep(60)\n")
    command.chmod(0o700)

    def failed_deadline(*_args):
        pytest.fail("recovery remained blocked by a stalled pg_ctl")

    with tempfile.TemporaryDirectory(prefix="ols-recovery-") as transport:
        postgres = LocalPostgres(stale, Path(transport) / "pg", binary)
        old_handler = signal.signal(signal.SIGALRM, failed_deadline)
        monkeypatch.setattr(
            sandbox_lifecycle, "POSTGRES_CONTROL_TIMEOUT_SECONDS", 0.1, raising=False
        )
        signal.alarm(3)
        try:
            try:
                with pytest.raises(subprocess.TimeoutExpired):
                    ScratchOwner(directory, fake_bin)
                assert (stale / "data/postmaster.pid").exists()
                with (
                    psycopg2.connect(postgres.dsn) as connection,
                    connection.cursor() as cursor,
                ):
                    cursor.execute("SELECT 1")
                    assert cursor.fetchone() == (1,)
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
                monkeypatch.undo()
            # A fresh owner acquires the lease and stops the retained live cluster.
            owner = ScratchOwner(directory, binary)
            try:
                assert not stale.exists()
            finally:
                owner.close()
        finally:
            postgres.close()


def test_recovery_removes_stale_postgres_directory_without_signalling_reused_pid(
    tmp_path,
):
    import os
    import subprocess
    import sys
    from pathlib import Path

    from sandbox_lifecycle import ScratchOwner

    directory = tmp_path / "villages"
    directory.mkdir(mode=0o700)
    stale = directory / "postgres-012345abcdef"
    data = stale / "data"
    data.mkdir(parents=True)
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=tmp_path,
        start_new_session=True,
    )
    try:
        (data / "postmaster.pid").write_text(str(unrelated.pid) + "\n")
        owner = ScratchOwner(
            directory,
            Path(
                os.environ.get(
                    "OPENLEG_SANDBOX_POSTGRES_BIN", "/usr/lib/postgresql/16/bin"
                )
            ),
        )
        try:
            assert not stale.exists()
            with pytest.raises(subprocess.TimeoutExpired):
                unrelated.wait(timeout=0.3)
        finally:
            owner.close()
    finally:
        if unrelated.poll() is None:
            unrelated.terminate()
        unrelated.wait(timeout=5)


@pytest.mark.parametrize("failure", ["runtime", "filesystem", "postgres", "capacity"])
def test_failed_replacement_clears_retired_binding_and_explains_retry(
    village, monkeypatch, failure
):
    import psycopg2

    client = village.test_client()
    prefix, _ = _start(client)
    manager = village.extensions["villages"]
    manager.max_workspaces = 1
    identifier = prefix.rsplit("/", 1)[1]
    old = manager.workspaces[identifier]
    confirmation = client.get(prefix + "/sandbox/manage")
    errors = {
        "runtime": RuntimeError,
        "filesystem": OSError,
        "postgres": psycopg2.OperationalError,
        "capacity": OverflowError,
    }

    def fail_start(*args, **kwargs):
        raise errors[failure]("synthetic private replacement diagnostic")

    with monkeypatch.context() as scoped:
        scoped.setattr(manager, "_provision", fail_start)
        response = client.post(
            prefix + "/sandbox/reset",
            data={
                "csrf_token": _csrf(confirmation),
                "confirm": "reset",
                "seed_version": "sonnwil/1",
            },
        )
    assert response.status_code == 503
    assert "private replacement diagnostic" not in response.text
    if failure == "capacity":
        assert "Alle Übungsdörfer sind gerade belegt." in response.text
        assert "Das Übungsdorf kann gerade nicht gestartet werden." not in response.text
    else:
        assert "Das Übungsdorf kann gerade nicht gestartet werden." in response.text
        assert "Alle Übungsdörfer sind gerade belegt." not in response.text
    with client.session_transaction() as session:
        assert "workspace" not in session
    assert client.get(prefix + "/dashboard").status_code == 410
    assert not old["directory"].exists()
    assert old["process"].poll() is not None
    with psycopg2.connect(manager.postgres.dsn) as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM pg_database WHERE datname=%s", (old["name"],)
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT COUNT(*) FROM pg_roles WHERE rolname=%s", (old["name"],))
        assert cursor.fetchone()[0] == 0
    assert not manager.workspaces
    assert not manager.retiring
    assert not manager.replacements
    fresh, page = _start(client)
    assert fresh != prefix
    assert page.status_code == 200


@pytest.mark.parametrize("initial,selected", [("de-CH", "fr-CH"), ("fr-CH", "de-CH")])
def test_language_selected_on_management_page_survives_return_to_worker(
    village, initial, selected
):
    client = village.test_client()
    client.get("/sandbox?lang=" + initial)
    prefix, _ = _start(client)
    management = client.get(prefix + "/sandbox/manage?lang=" + selected)
    assert f'<html lang="{selected}"' in management.text
    link = re.search(
        r'href="(' + prefix + r'/sandbox/home[^\"]*)"', management.text
    ).group(1)
    assert f'<html lang="{selected}"' in client.get(link).text
    assert f'<html lang="{selected}"' in client.get(prefix + "/dashboard").text
