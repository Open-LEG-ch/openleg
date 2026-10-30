# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-only release mode: OPENLEG_RELEASE_READ_ONLY gates the app and the pool.

Flag ``1`` puts a deployed release into a validation mode where the shared app
guard serves safe public GET/HEAD paths only and the database layer refuses
every write at the server, not just at the HTTP boundary. Any other nonempty
value must fail startup. ``0`` and unset keep normal behavior.
"""

import os
import secrets
import threading
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import psycopg2.extras
import psycopg2.pool
import pytest

import database as db
from tests import test_root_role_access
from tests.test_app_organic_routes import _disable_rate_limit_hooks

app_module = test_root_role_access.app_module

APP_CONFIG = {
    "TESTING": True,
    "APP_BASE_URL": "http://localhost:5003",
    "RATELIMIT_STORAGE_URI": "memory://",
}

BLOCKED_GET_PATHS = [
    "/confirm/test",
    "/interest/confirm/test",
    "/unsubscribe/test",
    "/utility/verify/test",
]


@pytest.fixture
def release_mode(monkeypatch):
    monkeypatch.setenv("OPENLEG_RELEASE_READ_ONLY", "1")


@pytest.fixture
def release_web(app_module, release_mode):
    web = app_module.create_app(
        APP_CONFIG, load_environment=False, check_database=False
    )
    hooks = _disable_rate_limit_hooks(web)
    try:
        yield web
    finally:
        web.before_request_funcs[None] = hooks


def _assert_guarded(response):
    assert response.status_code == 503
    assert response.headers.get("Retry-After") == "60"
    assert response.headers.get("Cache-Control") == "no-store"


@pytest.mark.parametrize("path", BLOCKED_GET_PATHS)
def test_private_magic_link_get_is_blocked_before_handler(release_web, path):
    _assert_guarded(release_web.test_client().get(path))


def test_post_registration_is_blocked_before_handler(release_web):
    _assert_guarded(
        release_web.test_client().post(
            "/api/register_interest", data={"email": "one@example.ch"}
        )
    )


def test_unknown_get_is_blocked_before_404(release_web):
    _assert_guarded(release_web.test_client().get("/definitely-not-a-route"))
    _assert_guarded(release_web.test_client().post("/definitely-not-a-route"))


@pytest.mark.parametrize("value", ["yes", "2", "readonly", " 1", "1 "])
def test_invalid_flag_fails_startup(app_module, monkeypatch, value):
    monkeypatch.setenv("OPENLEG_RELEASE_READ_ONLY", value)
    with pytest.raises(RuntimeError):
        app_module.create_app(APP_CONFIG, load_environment=False, check_database=False)


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/login",
        "/livez",
        "/static/js/form_validation.js",
        "/how-it-works",
        "/fuer-bewohner",
        "/fuer-gemeinden",
        "/open-source",
        "/leg-gruenden",
        "/leg-kalkulator",
        "/pricing",
        "/impressum",
        "/datenschutz",
        "/self-host",
        "/rangliste",
        "/rangliste/methodik",
        "/robots.txt",
        "/sitemap.xml",
        "/llms.txt",
        "/favicon.ico",
    ],
)
def test_safe_public_paths_stay_open_in_read_only_mode(release_web, path):
    response = release_web.test_client().get(path)
    assert response.status_code == 200


def test_safe_head_stays_open_in_read_only_mode(release_web):
    assert release_web.test_client().head("/").status_code == 200


@pytest.fixture
def release_database(monkeypatch, release_mode):
    """Unique native database whose only pool is opened under the flag."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("requires PostgreSQL DATABASE_URL")
    parsed = urlsplit(url)
    name = f"openleg_release_{secrets.token_hex(6)}"
    admin = psycopg2.connect(urlunsplit(parsed._replace(path="/postgres")))
    admin.autocommit = True
    pool = None
    probe_sql = "CREATE TABLE release_probe (id integer PRIMARY KEY, note text)"
    try:
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{name}"')
        pool = psycopg2.pool.ThreadedConnectionPool(
            1,
            6,
            urlunsplit(parsed._replace(path=f"/{name}")),
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        monkeypatch.setattr(db, "_connection_pool", pool)
        monkeypatch.setattr(db, "_pool_pid", os.getpid())
        monkeypatch.setattr(
            db, "DATABASE_URL", urlunsplit(parsed._replace(path=f"/{name}"))
        )
        monkeypatch.setattr(db, "_db_initialized", True)
        # Fixture setup is deliberately writable, before guarded store access.
        with psycopg2.connect(db.DATABASE_URL) as conn, conn.cursor() as cur:
            cur.execute(probe_sql)
        yield db
    finally:
        if pool:
            pool.closeall()
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.close()


def _insert_probe(db_module, note, probe_id=1):
    with db_module.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO release_probe (id, note) VALUES (%s, %s)", (probe_id, note)
        )


def test_select_survives_but_insert_is_refused(release_database):
    with release_database.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 AS ok")
        assert cur.fetchone()["ok"] == 1
    with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
        _insert_probe(release_database, "blocked")


def test_simultaneous_checkouts_all_refuse_writes(release_database):
    errors = []
    backend_pids = []
    barrier = threading.Barrier(3)

    def attempt(index):
        try:
            with release_database.get_connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT pg_backend_pid() AS pid")
                backend_pids.append(cur.fetchone()["pid"])
                barrier.wait(timeout=10)
                cur.execute(
                    "INSERT INTO release_probe(id,note) VALUES(%s,'concurrent')",
                    (index,),
                )
        except psycopg2.errors.ReadOnlySqlTransaction as exc:
            errors.append(exc)

    threads = [threading.Thread(target=attempt, args=(index,)) for index in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert all(not thread.is_alive() for thread in threads)
    assert len(set(backend_pids)) == 3
    assert len(errors) == 3


@pytest.mark.integration
@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
def test_forked_child_reopens_pool_and_refuses_writes(monkeypatch, release_database):
    with release_database.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_backend_pid() AS pid")
        parent_pid = cur.fetchone()["pid"]
    read_end, write_end = os.pipe()
    child = os.fork()
    if child == 0:
        status = 1
        try:
            os.close(read_end)
            with db.get_connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT pg_backend_pid() AS pid")
                child_pid = cur.fetchone()["pid"]
            try:
                _insert_probe(db, "from-child")
            except psycopg2.errors.ReadOnlySqlTransaction:
                outcome = f"{child_pid}|insert-refused"
            else:
                outcome = f"{child_pid}|insert-ok"
            os.write(write_end, outcome.encode())
            status = 0
        finally:
            os._exit(status)
    os.close(write_end)
    _, status = os.waitpid(child, 0)
    with os.fdopen(read_end) as pipe:
        outcome = pipe.read().split("|")
    assert os.waitstatus_to_exitcode(status) == 0
    assert int(outcome[0]) != parent_pid
    assert outcome[1] == "insert-refused"
    with release_database.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_backend_pid() AS pid")
        assert cur.fetchone()["pid"] == parent_pid


@pytest.fixture
def flag_zero(monkeypatch):
    monkeypatch.setenv("OPENLEG_RELEASE_READ_ONLY", "0")


def test_flag_zero_allows_real_insert(release_database_no_flag, flag_zero):
    _insert_probe(release_database_no_flag, "control")


@pytest.fixture
def release_database_no_flag(monkeypatch):
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("requires PostgreSQL DATABASE_URL")
    parsed = urlsplit(url)
    name = f"openleg_release_{secrets.token_hex(6)}"
    admin = psycopg2.connect(urlunsplit(parsed._replace(path="/postgres")))
    admin.autocommit = True
    pool = None
    try:
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{name}"')
        pool = psycopg2.pool.ThreadedConnectionPool(
            1,
            6,
            urlunsplit(parsed._replace(path=f"/{name}")),
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        monkeypatch.setattr(db, "_connection_pool", pool)
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE release_probe (id integer PRIMARY KEY, note text)"
            )
        yield db
    finally:
        if pool:
            pool.closeall()
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.close()


def test_readonly_init_skips_schema_and_seed(monkeypatch, release_mode, isolated_init):
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("requires PostgreSQL DATABASE_URL")
    created = []
    seeded = []
    monkeypatch.setattr(db, "DATABASE_URL", url)
    monkeypatch.setattr(db, "_db_initialized", False)
    monkeypatch.setattr(db, "create_tables", lambda: created.append("tables"))
    monkeypatch.setattr(db, "seed_default_tenant", lambda: seeded.append("tenant"))
    assert db.is_db_available()
    assert created == []
    assert seeded == []


def test_normal_init_still_creates_schema_and_seeds(
    monkeypatch, flag_zero, isolated_init
):
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("requires PostgreSQL DATABASE_URL")
    created = []
    seeded = []
    monkeypatch.setattr(db, "DATABASE_URL", url)
    monkeypatch.setattr(db, "_db_initialized", False)
    monkeypatch.setattr(db, "create_tables", lambda: created.append("tables"))
    monkeypatch.setattr(db, "seed_default_tenant", lambda: seeded.append("tenant"))
    assert db.is_db_available()
    assert created == ["tables"]
    assert seeded == ["tenant"]


@pytest.fixture
def isolated_init(monkeypatch):
    monkeypatch.setattr(db, "_connection_pool", None)
    monkeypatch.setattr(db, "_pool_pid", None)
    monkeypatch.setattr(db, "_db_initialized", False)
    try:
        yield
    finally:
        if db._connection_pool is not None:
            db._connection_pool.closeall()


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/site/home",
        "/api/v1/docs",
        "/api/v1/municipalities",
        "/install.sh",
        "/pilotgemeinde/baden",
        "/gemeinde/verzeichnis",
        "/rangliste/fortschritte",
        "/rangliste/og/261.svg",
        "/leg-check",
    ],
)
def test_public_handlers_outside_release_proof_surface_are_blocked(release_web, path):
    _assert_guarded(release_web.test_client().get(path))


@pytest.mark.parametrize(
    "method", ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
)
def test_private_routes_are_blocked_for_every_method(release_web, method):
    _assert_guarded(release_web.test_client().open("/dashboard", method=method))


def test_guard_runs_before_locale_tenant_and_route_handler(release_web, monkeypatch):
    import app

    def forbidden(*args, **kwargs):
        pytest.fail("blocked request reached a later hook or handler")

    monkeypatch.setattr(app.i18n, "normalize_locale", forbidden)
    monkeypatch.setattr(app.tenant_module, "get_tenant_config", forbidden)
    endpoint = release_web.url_map.bind("localhost").match("/confirm/test")[0]
    monkeypatch.setitem(release_web.view_functions, endpoint, forbidden)
    _assert_guarded(release_web.test_client().get("/confirm/test?lang=fr-CH"))


def test_reused_native_connection_returns_to_normal_then_read_only(
    release_database, monkeypatch
):
    with release_database.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SHOW transaction_read_only")
        assert cur.fetchone()["transaction_read_only"] == "on"
    monkeypatch.setenv("OPENLEG_RELEASE_READ_ONLY", "0")
    _insert_probe(release_database, "normal reset")
    monkeypatch.setenv("OPENLEG_RELEASE_READ_ONLY", "1")
    with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
        _insert_probe(release_database, "guard restored", probe_id=2)


def test_native_readonly_init_does_not_create_or_seed_schema(
    release_database, monkeypatch
):
    original_pool = db._connection_pool
    with monkeypatch.context() as patch:
        patch.setattr(db, "_connection_pool", None)
        patch.setattr(db, "_pool_pid", None)
        patch.setattr(db, "_db_initialized", False)
        try:
            assert db.is_db_available()
            assert db._connection_pool is not original_pool
            with db.get_connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                assert [row["tablename"] for row in cur.fetchall()] == ["release_probe"]
            with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
                _insert_probe(db, "new init remains readonly")
        finally:
            if db._connection_pool is not None:
                db._connection_pool.closeall()


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_public_proof_routes_allow_only_get_and_head(release_web, method):
    _assert_guarded(release_web.test_client().open("/", method=method))


@pytest.mark.parametrize("value", [None, "", "0"])
def test_normal_values_preserve_private_handlers(app_module, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("OPENLEG_RELEASE_READ_ONLY", raising=False)
    else:
        monkeypatch.setenv("OPENLEG_RELEASE_READ_ONLY", value)
    web = app_module.create_app(
        APP_CONFIG, load_environment=False, check_database=False
    )
    endpoint = web.url_map.bind("localhost").match("/confirm/test")[0]
    monkeypatch.setitem(
        web.view_functions, endpoint, lambda token: ("normal handler", 200)
    )
    assert web.test_client().get("/confirm/test").status_code == 200


def test_health_reads_the_live_readonly_pool(release_web, release_database):
    response = release_web.test_client().get("/health")
    assert response.status_code == 200
    assert response.get_json()["db"] == "connected"


def test_public_municipality_profile_handler_is_allowed(release_web, monkeypatch):
    monkeypatch.setitem(
        release_web.view_functions,
        "municipality.profil",
        lambda bfs: ("synthetic public profile", 200),
    )
    assert release_web.test_client().get("/gemeinde/profil/261").status_code == 200
