# SPDX-License-Identifier: AGPL-3.0-or-later
"""One-time GRD links across concurrent, real PostgreSQL requests."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier

import pytest

from tests import test_interest_postgres
from tests.test_i18n import _synthetic_environment

interest_database = test_interest_postgres.interest_database

pytestmark = pytest.mark.integration


def make_web():
    with _synthetic_environment():
        import app

        return app.create_app(
            {"TESTING": True, "RATELIMIT_STORAGE_URI": "memory://"},
            load_environment=False,
            check_database=False,
        )


def seed(db):
    assert db.save_utility_client(
        "synthetic-grid", "Synthetic grid", "grid@example.invalid"
    )
    assert db.update_utility_client_status("synthetic-grid", "active")
    assert db.set_utility_magic_token("synthetic-grid", "synthetic-original", 900)


def test_two_concurrent_requests_create_exactly_one_session(
    interest_database, monkeypatch
):
    db = interest_database
    seed(db)
    web = make_web()
    connection = db.get_connection
    ready = Barrier(2)
    old_read_ready = Barrier(2)
    old_read = db.get_utility_client_by_magic_token

    def synchronized_legacy_read(token):
        row = old_read(token)
        old_read_ready.wait(timeout=10)
        return row

    monkeypatch.setattr(
        db, "get_utility_client_by_magic_token", synchronized_legacy_read
    )

    def login(_):
        client = web.test_client()
        response = client.get("/utility/login?token=synthetic-original")
        with client.session_transaction() as session:
            return response.status_code, session.get("utility_client_id"), response.text

    # Legacy flow opens a second connection to clear; synchronize only the
    # initial two connections, so it reproduces the double-login defect too.
    calls = 0
    from threading import Lock

    lock = Lock()

    @contextmanager
    def first_connections_only():
        nonlocal calls
        with connection() as conn:
            with lock:
                calls += 1
                initial = calls <= 2
            if initial:
                ready.wait(timeout=10)
            yield conn

    monkeypatch.setattr(db, "get_connection", first_connections_only)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(login, [0, 1]))
    assert sum(identity is not None for _, identity, _ in results) == 1
    winner = next(row for row in results if row[1])
    loser = next(row for row in results if not row[1])
    assert winner[:2] == (302, "synthetic-grid")
    assert loser[0] == 200
    assert "Ungültiger oder abgelaufener Login-Link" in loser[2]
    assert "synthetic-grid" not in loser[2]


def test_replaced_and_expired_links_cannot_clear_current_token(interest_database):
    db = interest_database
    seed(db)
    assert db.set_utility_magic_token("synthetic-grid", "synthetic-replacement", 900)
    client = make_web().test_client()
    assert client.get("/utility/login?token=synthetic-original").status_code == 200
    assert (
        db.get_utility_client("synthetic-grid")["magic_link_token"]
        == "synthetic-replacement"
    )
    assert client.get("/utility/login?token=synthetic-replacement").status_code == 302
    assert db.get_utility_client("synthetic-grid")["magic_link_token"] is None
    assert db.set_utility_magic_token("synthetic-grid", "synthetic-expired", -1)
    other = make_web().test_client()
    assert other.get("/utility/login?token=synthetic-expired").status_code == 200
    with other.session_transaction() as session:
        assert "utility_client_id" not in session
    assert (
        db.get_utility_client("synthetic-grid")["magic_link_token"]
        == "synthetic-expired"
    )
    # A rejected token does not log out an already authenticated account.
    assert client.get("/utility/login?token=synthetic-expired").status_code == 200
    with client.session_transaction() as session:
        assert session["utility_client_id"] == "synthetic-grid"


def test_concurrent_replacement_survives_an_older_waiting_request(
    interest_database, monkeypatch
):
    import time
    from threading import Event

    db = interest_database
    seed(db)
    web = make_web()
    entered = Event()
    real_connection = db.get_connection
    with real_connection() as blocker, blocker.cursor() as cur:
        cur.execute(
            "UPDATE utility_clients SET magic_link_token = %s WHERE client_id = %s",
            ("synthetic-replacement", "synthetic-grid"),
        )

        @contextmanager
        def worker_connection():
            with real_connection() as conn:
                entered.set()
                yield conn

        monkeypatch.setattr(db, "get_connection", worker_connection)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                web.test_client().get, "/utility/login?token=synthetic-original"
            )
            assert entered.wait(timeout=10)
            # Observe a real row-lock wait, not a sleep-based approximation.
            deadline = time.monotonic() + 10
            while True:
                cur.execute(
                    "SELECT count(*) AS count FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
                if cur.fetchone()["count"]:
                    break
                assert time.monotonic() < deadline, (
                    "the token request never reached PostgreSQL's row lock"
                )
                time.sleep(0.01)
            blocker.commit()
            assert future.result(timeout=10).status_code == 200
    assert (
        db.get_utility_client("synthetic-grid")["magic_link_token"]
        == "synthetic-replacement"
    )
