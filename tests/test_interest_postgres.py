# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verified-interest behavior through the real PostgreSQL connection seam."""

import os
import secrets
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import psycopg2.extras
import psycopg2.pool
import pytest

import database as db


@pytest.fixture
def interest_database(monkeypatch):
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("requires PostgreSQL DATABASE_URL")
    parsed = urlsplit(url)
    name = f"openleg_interest_{secrets.token_hex(6)}"
    admin = psycopg2.connect(urlunsplit(parsed._replace(path="/postgres")))
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    pool = None
    try:
        pool = psycopg2.pool.ThreadedConnectionPool(
            1,
            6,
            urlunsplit(parsed._replace(path=f"/{name}")),
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        monkeypatch.setattr(db, "_connection_pool", pool)
        db.create_tables()
        yield db
    finally:
        if pool:
            pool.closeall()
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.close()


def save_registration(email="one@example.ch", **kwargs):
    return db.save_building(
        building_id="interest-building",
        email=email,
        profile={
            "address": "Testweg 1",
            "lat": 47.2,
            "lon": 8.2,
            "bfs_number": 2554,
            "municipality_name": "Riedholz",
        },
        consents={},
        **kwargs,
    )


@pytest.mark.integration
def test_same_email_registration_keeps_verified_state_and_timestamp(interest_database):
    assert save_registration()
    assert db.update_building_verified("interest-building")
    before = db.get_building("interest-building")
    assert save_registration("ONE@example.ch")
    after = db.get_building("interest-building")
    assert after["verified"] is True
    assert after["verified_at"] == before["verified_at"]


@pytest.mark.integration
def test_old_email_link_cannot_verify_a_replacement_email(interest_database):
    old_token, new_token = str(uuid.uuid4()), str(uuid.uuid4())
    assert save_registration(verification_token=old_token)
    assert save_registration("new@example.ch", verification_token=new_token)
    assert db.confirm_building_interest(old_token) is None
    assert db.get_building("interest-building")["verified"] is False
    confirmed = db.confirm_building_interest(new_token)
    assert confirmed["email"] == "new@example.ch"
    assert db.get_building("interest-building")["verified"] is True
    assert db.confirm_building_interest(new_token) is None


@dataclass
class _DatabaseWorker:
    operation: object
    pause_before_commit: bool = False
    connected: threading.Event = field(default_factory=threading.Event)
    staged: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    pid: int | None = None
    result: object = None
    error: BaseException | None = None
    thread: threading.Thread | None = None


class _TransactionRace:
    def __init__(self, original_connection):
        self.original_connection = original_connection
        self.local = threading.local()
        self.workers = []

    @contextmanager
    def connection(self):
        worker = getattr(self.local, "worker", None)
        # The original context still owns commit, rollback and pool return.
        with self.original_connection() as connection:
            if worker is not None:
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL statement_timeout = '10s'")
                    cursor.execute("SELECT pg_backend_pid() AS pid")
                    worker.pid = cursor.fetchone()["pid"]
                worker.connected.set()
            yield connection
            if worker is not None and worker.pause_before_commit:
                worker.staged.set()
                if not worker.release.wait(timeout=10):
                    raise TimeoutError("test did not release the staged transaction")

    def start(self, operation, *, pause_before_commit=False):
        worker = _DatabaseWorker(operation, pause_before_commit)
        self.workers.append(worker)

        def run():
            self.local.worker = worker
            try:
                worker.result = worker.operation()
            except BaseException as error:
                worker.error = error
            finally:
                worker.done.set()

        worker.thread = threading.Thread(target=run, daemon=True)
        worker.thread.start()
        assert worker.connected.wait(timeout=5), "worker never opened its transaction"
        return worker

    def wait_until_staged(self, worker):
        assert worker.staged.wait(timeout=5), (
            f"first transaction did not reach its commit point: {worker.error!r}"
        )
        assert not worker.done.is_set()

    def wait_until_blocked_by(self, waiter, blocker):
        deadline = time.monotonic() + 5
        last_observation = None
        while time.monotonic() < deadline:
            assert not waiter.done.is_set(), (
                "competing call finished without waiting on the first transaction: "
                f"result={waiter.result!r}, error={waiter.error!r}"
            )
            # A fresh transaction on each observation avoids a cached stats snapshot.
            with (
                self.original_connection() as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    """
                    SELECT wait_event_type, wait_event,
                           pg_blocking_pids(pid) AS blocking_pids
                    FROM pg_stat_activity
                    WHERE pid = %s
                    """,
                    (waiter.pid,),
                )
                last_observation = cursor.fetchone()
            if (
                last_observation
                and last_observation["wait_event_type"] == "Lock"
                and blocker.pid in last_observation["blocking_pids"]
            ):
                return
            # Yield while waiting for an observed condition; no assumed startup delay.
            waiter.done.wait(timeout=0.01)
        raise AssertionError(f"expected PostgreSQL lock wait; saw {last_observation!r}")

    def finish(self, worker):
        worker.thread.join(timeout=5)
        assert not worker.thread.is_alive(), "database worker did not finish"
        if worker.error is not None:
            raise worker.error
        return worker.result

    def close(self):
        for worker in self.workers:
            worker.release.set()
        for worker in self.workers:
            worker.thread.join(timeout=2)
        running = [worker for worker in self.workers if worker.thread.is_alive()]
        for worker in running:
            if worker.pid is not None:
                with (
                    self.original_connection() as connection,
                    connection.cursor() as cursor,
                ):
                    cursor.execute("SELECT pg_cancel_backend(%s)", (worker.pid,))
        for worker in running:
            worker.thread.join(timeout=3)
        assert all(not worker.thread.is_alive() for worker in self.workers), (
            "test cleanup left a database worker running"
        )


@contextmanager
def transaction_race(monkeypatch):
    race = _TransactionRace(db.get_connection)
    with monkeypatch.context() as patch:
        patch.setattr(db, "get_connection", race.connection)
        try:
            yield race
        finally:
            race.close()


@pytest.mark.integration
def test_confirmation_commits_before_re_registration_rejects_other_email(
    interest_database, monkeypatch
):
    old_token, new_token = str(uuid.uuid4()), str(uuid.uuid4())
    assert save_registration(verification_token=old_token)

    with transaction_race(monkeypatch) as race:
        confirmation = race.start(
            lambda: db.confirm_building_interest(old_token), pause_before_commit=True
        )
        race.wait_until_staged(confirmation)
        registration = race.start(
            lambda: save_registration("new@example.ch", verification_token=new_token)
        )
        race.wait_until_blocked_by(registration, confirmation)
        confirmation.release.set()
        confirmed = race.finish(confirmation)
        with pytest.raises(db.VerifiedRegistrationConflict):
            race.finish(registration)

    # The returned snapshot belongs to the identity confirmed in that transaction.
    assert confirmed["email"] == "one@example.ch"
    building = db.get_building("interest-building")
    assert building["email"] == "one@example.ch"
    assert building["verified"] is True
    assert building["verified_at"] == confirmed["verified_at"]
    assert db.confirm_building_interest(old_token) is None
    assert db.confirm_building_interest(new_token) is None


@pytest.mark.integration
def test_re_registration_commits_before_old_confirmation_is_rejected(
    interest_database, monkeypatch
):
    old_token, new_token = str(uuid.uuid4()), str(uuid.uuid4())
    assert save_registration(verification_token=old_token)

    with transaction_race(monkeypatch) as race:
        registration = race.start(
            lambda: save_registration("new@example.ch", verification_token=new_token),
            pause_before_commit=True,
        )
        race.wait_until_staged(registration)
        confirmation = race.start(lambda: db.confirm_building_interest(old_token))
        race.wait_until_blocked_by(confirmation, registration)
        registration.release.set()
        assert race.finish(registration) is True
        assert race.finish(confirmation) is None

    building = db.get_building("interest-building")
    assert building["email"] == "new@example.ch"
    assert building["verified"] is False
    assert building["verified_at"] is None
    assert db.confirm_building_interest(new_token)["email"] == "new@example.ch"


@pytest.mark.integration
def test_concurrent_confirmation_consumes_one_token_once(
    interest_database, monkeypatch
):
    token = str(uuid.uuid4())
    assert save_registration(verification_token=token)

    with transaction_race(monkeypatch) as race:
        first = race.start(
            lambda: db.confirm_building_interest(token), pause_before_commit=True
        )
        race.wait_until_staged(first)
        second = race.start(lambda: db.confirm_building_interest(token))
        race.wait_until_blocked_by(second, first)
        first.release.set()
        assert race.finish(first)["email"] == "one@example.ch"
        assert race.finish(second) is None

    assert db.get_building("interest-building")["verified"] is True
    assert db.confirm_building_interest(token) is None


@pytest.mark.integration
def test_failed_verification_rolls_back_token_consumption(interest_database):
    token = str(uuid.uuid4())
    assert save_registration(verification_token=token)
    with db.get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE FUNCTION test_reject_verification() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.verified IS TRUE AND OLD.verified IS DISTINCT FROM TRUE THEN
                    RAISE EXCEPTION 'test verification write rejected';
                END IF;
                RETURN NEW;
            END;
            $$;
            CREATE TRIGGER test_reject_verification
            BEFORE UPDATE ON buildings
            FOR EACH ROW EXECUTE FUNCTION test_reject_verification();
            """
        )

    with pytest.raises(db.VerificationConflict):
        db.confirm_building_interest(token)
    building = db.get_building("interest-building")
    assert building["verified"] is False
    assert building["verified_at"] is None
    assert db.get_token(token) is not None

    with db.get_connection() as connection, connection.cursor() as cursor:
        cursor.execute("DROP TRIGGER test_reject_verification ON buildings")
    assert db.confirm_building_interest(token)["email"] == "one@example.ch"


@pytest.mark.integration
@pytest.mark.parametrize("existing_registration", [False, True])
def test_failed_token_insert_rolls_back_registration(
    interest_database, existing_registration
):
    if existing_registration:
        assert save_registration()
        assert db.update_building_verified("interest-building")
    before = db.get_building("interest-building")

    with db.get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE FUNCTION test_reject_verification_token() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.token_type = 'verification' THEN
                    RAISE EXCEPTION 'test token insert rejected';
                END IF;
                RETURN NEW;
            END;
            $$;
            CREATE TRIGGER test_reject_verification_token
            BEFORE INSERT ON tokens
            FOR EACH ROW EXECUTE FUNCTION test_reject_verification_token();
            """
        )

    token = str(uuid.uuid4())
    assert (
        db.save_building(
            building_id="interest-building",
            email="one@example.ch" if existing_registration else "new@example.ch",
            profile={
                "address": "Testweg 2",
                "lat": 47.2,
                "lon": 8.2,
                "bfs_number": 2554,
                "municipality_name": "Riedholz",
            },
            consents={"share_with_neighbors": True},
            verification_token=token,
        )
        is False
    )

    assert db.get_building("interest-building") == before
    assert db.get_token(token) is None
    with db.get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) AS count FROM consents WHERE building_id = %s",
            ("interest-building",),
        )
        assert cursor.fetchone()["count"] == int(existing_registration)


@pytest.mark.integration
@pytest.mark.parametrize("with_token", [False, True])
def test_verified_email_guard_applies_to_every_save_path(interest_database, with_token):
    assert save_registration(verified=True)
    before = db.get_building("interest-building")
    token = str(uuid.uuid4()) if with_token else None
    with pytest.raises(db.VerifiedRegistrationConflict):
        save_registration("other@example.ch", verification_token=token)
    assert db.get_building("interest-building") == before
    if token:
        assert db.get_token(token) is None
