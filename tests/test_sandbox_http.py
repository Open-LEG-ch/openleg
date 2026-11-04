# SPDX-License-Identifier: AGPL-3.0-or-later
"""Drive independent disposable villages through their HTTP entry point."""

import os
import re
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from uuid import uuid4

import psycopg2
import pytest
from psycopg2 import sql
from psycopg2.extensions import make_dsn


@pytest.fixture
def village(tmp_path, request):
    from sandbox_runtime import create_gateway

    postgres_bin = os.environ.get(
        "OPENLEG_SANDBOX_POSTGRES_BIN", "/usr/lib/postgresql/16/bin"
    )
    if not (Path(postgres_bin) / "initdb").is_file():
        pytest.skip("requires local PostgreSQL 16 binaries")
    gateway = create_gateway(
        tmp_path / "villages",
        postgres_bin=postgres_bin,
        max_workspaces=2,
        **getattr(request, "param", {}),
    )
    gateway.config["TESTING"] = True
    try:
        yield gateway
    finally:
        gateway.extensions["villages"].close()


def _csrf(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)


def _enter(client):
    page = client.get("/sandbox")
    assert page.status_code == 200
    response = client.post(
        "/sandbox/start", data={"csrf_token": _csrf(page), "persona": "resident"}
    )
    assert response.status_code == 302
    dashboard = client.get(response.location, follow_redirects=True)
    assert dashboard.status_code == 200
    assert "Sonnwil" in dashboard.text
    assert "Simulation" in dashboard.text
    return dashboard


@pytest.mark.integration
def test_two_visitors_get_independent_writable_villages(village):
    first, second = village.test_client(), village.test_client()
    first_page = _enter(first)
    second_page = _enter(second)
    simultaneous = Barrier(2)

    def save(client, page, consumption):
        simultaneous.wait(timeout=5)
        return client.post(
            "/dashboard/profile",
            data={
                "csrf_token": _csrf(page),
                "annual_consumption_kwh": consumption,
                "potential_pv_kwp": "8",
                "share_with_neighbors": "on",
            },
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        writes = [
            executor.submit(save, first, first_page, "7200"),
            executor.submit(save, second, second_page, "5800"),
        ]
        assert [write.result(timeout=10) for write in writes] == [302, 302]
    assert first.get("/dashboard/export").json["annual_consumption_kwh"] == 7200
    assert second.get("/dashboard/export").json["annual_consumption_kwh"] == 5800
    assert "7200" in first.get("/dashboard").text
    assert first.get("/dashboard").headers["Cache-Control"] == "no-store"

    # The worker cookie alone cannot authenticate in a different village.
    cookie = first.get_cookie("openleg_product")
    assert cookie is not None
    second.set_cookie("openleg_product", cookie.value)
    assert second.get("/dashboard/export").status_code == 401

    # Capacity is bounded and an unowned worker path is never an alternate login.
    third = village.test_client()
    page = third.get("/sandbox")
    assert (
        third.post(
            "/sandbox/start", data={"csrf_token": _csrf(page), "persona": "resident"}
        ).status_code
        == 503
    )
    assert third.get("/dashboard?workspace=resident").status_code == 303


@pytest.mark.integration
def test_access_email_is_delivered_only_to_the_visitors_simulated_inbox(village):
    first, second = village.test_client(), village.test_client()
    first_page = _enter(first)
    _enter(second)
    assert (
        first.post(
            "/dashboard/logout", data={"csrf_token": _csrf(first_page)}
        ).status_code
        == 302
    )
    response = first.post(
        "/dashboard/access/request", data={"email": "sonnwil-resident@example.org"}
    )
    assert response.status_code == 200
    inbox = first.get("/sandbox/inbox")
    assert inbox.status_code == 200
    assert "sonnwil-resident@example.org" in inbox.text
    link = re.search(r'href="(/dashboard/access/[^"?]+)', inbox.text).group(1)
    assert link not in second.get("/sandbox/inbox").text
    assert second.get(link).location.endswith("access=invalid")
    assert first.get("/dashboard/export").status_code == 401
    assert first.get(link).location == "/dashboard"
    assert first.get("/dashboard/export").json["building_id"] == "sonnwil-resident"
    assert first.get(link).location.endswith("access=invalid")


@pytest.mark.integration
def test_gateway_rejects_untrusted_host_and_cross_site_start(village):
    client = village.test_client()
    assert (
        client.get("/sandbox", headers={"Host": "untrusted.example.org"}).status_code
        == 400
    )
    assert (
        client.post("/sandbox/start", data={"persona": "resident"}).status_code == 400
    )


@pytest.mark.integration
def test_french_entry_keeps_the_selected_language_when_entering_the_village(village):
    client = village.test_client()
    page = client.get("/sandbox?lang=fr-CH")
    assert "Votre village d&#39;essai" in page.text
    response = client.post(
        "/sandbox/start",
        data={"csrf_token": _csrf(page), "persona": "resident"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert '<html lang="fr-CH">' in response.text
    assert "Village d&#39;essai" in response.text


@pytest.mark.integration
@pytest.mark.parametrize(
    "village", [{"origin": "https://sandbox.example.org"}], indirect=True
)
def test_https_gateway_enters_a_village_without_internal_redirects(village):
    client = village.test_client()
    origin = "https://sandbox.example.org"
    page = client.get("/sandbox", base_url=origin)
    response = client.post(
        "/sandbox/start",
        base_url=origin,
        data={"csrf_token": _csrf(page), "persona": "resident"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Simulation · Sonnwil" in response.text
    assert client.get_cookie("openleg_product", domain="sandbox.example.org").secure


@pytest.mark.integration
def test_restarting_a_product_worker_keeps_the_visitors_consent_choices(village):
    client = village.test_client()
    page = _enter(client)
    response = client.post(
        "/dashboard/profile",
        data={
            "csrf_token": _csrf(page),
            "annual_consumption_kwh": "7200",
            "share_with_utility": "on",
        },
    )
    assert response.status_code == 302
    with client.session_transaction() as state:
        identifier = state["workspace"]
    master = village.extensions["villages"].workspaces[identifier]["process"]
    children = Path(f"/proc/{master.pid}/task/{master.pid}/children")
    original_children = set(children.read_text().split())
    master.send_signal(signal.SIGHUP)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        current_children = set(children.read_text().split())
        if current_children and not current_children.intersection(original_children):
            break
        time.sleep(0.05)
    else:
        pytest.fail("product worker did not restart")
    profile = client.get("/dashboard/export").json
    assert profile["annual_consumption_kwh"] == 7200
    assert profile["share_with_neighbors"] is False
    assert profile["share_with_utility"] is True


@pytest.mark.integration
@pytest.mark.parametrize("village", [{"max_upload_bytes": 65536}], indirect=True)
def test_configured_upload_bound_is_enforced_before_forwarding_to_a_worker(village):
    client = village.test_client()
    _enter(client)
    response = client.post("/dashboard/profile", data=b"x" * 65537)
    assert response.status_code == 413
    assert client.get("/dashboard/export").json["annual_consumption_kwh"] == 4500


@pytest.mark.integration
def test_worker_requires_explicit_mode_even_with_an_otherwise_valid_workspace(
    village, tmp_path
):
    postgres = village.extensions["villages"].postgres
    name = "ols_probe_" + uuid4().hex
    connection = psycopg2.connect(postgres.dsn)
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE DATABASE {} OWNER sandbox_admin").format(
                    sql.Identifier(name)
                )
            )
    finally:
        connection.close()
    directory = tmp_path / "probe"
    directory.mkdir(mode=0o700)
    settings = {
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "DATABASE_URL": make_dsn(postgres.dsn, dbname=name),
        "OPENLEG_SANDBOX_DIRECTORY": str(directory),
        "OPENLEG_SANDBOX_WORKSPACE": uuid4().hex,
        "OPENLEG_SANDBOX_MAX_UPLOAD_BYTES": "65536",
        "SECRET_KEY": "synthetic-probe-session-key",
        "APP_BASE_URL": "http://localhost",
        "PUBLIC_SITE_URL": "http://localhost",
        "REDIS_URL": "memory://",
        "DB_POOL_MIN": "1",
        "DB_POOL_MAX": "2",
    }
    enabled = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sandbox_worker; import socket; socket.socket(socket.AF_INET, socket.SOCK_STREAM)",
        ],
        cwd=directory,
        env={**settings, "OPENLEG_SANDBOX_WORKER": "1"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert enabled.returncode != 0
    assert "PermissionError" in enabled.stderr
    for mode in (None, "0"):
        environment = (
            settings if mode is None else {**settings, "OPENLEG_SANDBOX_WORKER": mode}
        )
        disabled = subprocess.run(
            [sys.executable, "-c", "import sandbox_worker"],
            cwd=directory,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert disabled.returncode != 0


@pytest.mark.integration
def test_workspace_database_roles_cannot_connect_to_another_village(village):
    _enter(village.test_client())
    _enter(village.test_client())
    manager = village.extensions["villages"]
    names = [workspace["name"] for workspace in manager.workspaces.values()]
    with (
        psycopg2.connect(manager.postgres.dsn) as connection,
        connection.cursor() as cursor,
    ):
        for owner in names:
            cursor.execute(
                "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = %s",
                (owner,),
            )
            assert cursor.fetchone() == (False, False, False)
            for database_name in [*names, "postgres", "template1"]:
                cursor.execute(
                    "SELECT has_database_privilege(%s, %s, 'CONNECT')",
                    (owner, database_name),
                )
                assert cursor.fetchone()[0] is (owner == database_name)


@pytest.mark.integration
def test_provisioning_a_new_village_does_not_block_active_visitors(
    village, monkeypatch
):
    first, second = village.test_client(), village.test_client()
    _enter(first)
    page = second.get("/sandbox")
    provisioning, release = Event(), Event()
    original = subprocess.Popen

    def pause_worker(*args, **kwargs):
        process = original(*args, **kwargs)
        if "sandbox_worker:application" in args[0]:
            provisioning.set()
            assert release.wait(timeout=10)
        return process

    monkeypatch.setattr("sandbox_runtime.subprocess.Popen", pause_worker)
    with ThreadPoolExecutor(max_workers=2) as executor:
        startup = executor.submit(
            second.post,
            "/sandbox/start",
            data={"csrf_token": _csrf(page), "persona": "resident"},
        )
        try:
            assert provisioning.wait(timeout=10)
            active = executor.submit(first.get, "/dashboard/export")
            assert active.result(timeout=2).json["annual_consumption_kwh"] == 4500
        finally:
            release.set()
        assert startup.result(timeout=30).status_code == 302


@pytest.mark.integration
def test_duplicate_start_for_one_visitor_reserves_one_workspace(village):
    first = village.test_client()
    page = first.get("/sandbox")
    cookie = first.get_cookie("openleg_sandbox")
    duplicate = village.test_client()
    duplicate.set_cookie("openleg_sandbox", cookie.value)
    simultaneous = Barrier(2)

    def start(client):
        simultaneous.wait(timeout=5)
        return client.post(
            "/sandbox/start", data={"csrf_token": _csrf(page), "persona": "resident"}
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(start, (first, duplicate)))
    assert [r.status_code for r in responses] == [302, 302]
    assert len(village.extensions["villages"].workspaces) == 1
    with first.session_transaction() as a, duplicate.session_transaction() as b:
        assert a["workspace"] == b["workspace"]


@pytest.mark.integration
def test_shutdown_cleans_every_worker_when_scratch_postgres_is_unavailable(village):
    _enter(village.test_client())
    _enter(village.test_client())
    manager = village.extensions["villages"]
    workspaces = list(manager.workspaces.values())
    manager.postgres.close()
    try:
        manager.close()
        assert all(workspace["process"].poll() is not None for workspace in workspaces)
        assert all(not workspace["directory"].exists() for workspace in workspaces)
        assert not manager.postgres.directory.exists()
        manager.close()
    finally:
        # A failing shutdown regression must not leave test-owned children alive.
        for workspace in workspaces:
            process = workspace["process"]
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
