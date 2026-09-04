# SPDX-License-Identifier: AGPL-3.0-or-later
"""PostgreSQL behaviour contracts for metering writes and the SDAT ledger."""

import os
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import psycopg2.extras
import psycopg2.pool
import pytest

import database
import store.metering

POINT = "CH000000000000000000000000000001"
MEASURED_AT = datetime(2026, 1, 5, 23, 0, tzinfo=timezone.utc)

METERING_SCHEMA = """
    CREATE TABLE metering_points (
        metering_point_id VARCHAR(64) PRIMARY KEY,
        vnb_community_id VARCHAR(64),
        community_id VARCHAR(64),
        building_id VARCHAR(64),
        alias VARCHAR(128),
        address TEXT,
        active BOOLEAN DEFAULT TRUE,
        expected_directions VARCHAR(16)[],
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE TABLE community_members (
        id SERIAL PRIMARY KEY,
        community_id VARCHAR(64),
        building_id VARCHAR(64),
        role VARCHAR(20) DEFAULT 'member',
        status VARCHAR(20) DEFAULT 'invited',
        UNIQUE (community_id, building_id)
    );
    CREATE TABLE metering_point_readings (
        id BIGSERIAL PRIMARY KEY,
        metering_point_id VARCHAR(64) NOT NULL
            REFERENCES metering_points(metering_point_id),
        direction VARCHAR(16) NOT NULL,
        measured_at TIMESTAMPTZ NOT NULL,
        resolution_minutes SMALLINT NOT NULL,
        total_kwh NUMERIC(12, 4),
        grid_kwh NUMERIC(12, 4),
        community_kwh NUMERIC(12, 4),
        condition_code VARCHAR(8),
        source_document_id VARCHAR(64),
        imported_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE (metering_point_id, direction, measured_at)
    );
    CREATE TABLE sdat_imports (
        id SERIAL PRIMARY KEY,
        document_id VARCHAR(64) NOT NULL UNIQUE,
        doc_type VARCHAR(8),
        file_name VARCHAR(255),
        vnb_community_id VARCHAR(64),
        document_created_at TIMESTAMPTZ,
        period_start TIMESTAMPTZ,
        period_end TIMESTAMPTZ,
        block_count INTEGER DEFAULT 0,
        row_count INTEGER DEFAULT 0,
        new_count INTEGER DEFAULT 0,
        corrected_count INTEGER DEFAULT 0,
        imported_at TIMESTAMPTZ DEFAULT NOW()
    )
"""


@contextmanager
def _temporary_database():
    original_url = os.environ["DATABASE_URL"]
    parsed = urlsplit(original_url)
    admin_url = urlunsplit(parsed._replace(path="/postgres"))
    db_name = f"openleg_metering_{secrets.token_hex(6)}"
    db_url = urlunsplit(parsed._replace(path=f"/{db_name}"))

    admin_conn = psycopg2.connect(admin_url)
    admin_conn.autocommit = True
    try:
        with admin_conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        admin_conn.close()

    try:
        yield db_url
    finally:
        admin_conn = psycopg2.connect(admin_url)
        admin_conn.autocommit = True
        try:
            with admin_conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s",
                    (db_name,),
                )
                cur.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        finally:
            admin_conn.close()


@contextmanager
def _pool_against(url):
    old_pool = database._connection_pool
    new_pool = psycopg2.pool.ThreadedConnectionPool(
        1, 2, url, cursor_factory=psycopg2.extras.RealDictCursor
    )
    database._connection_pool = new_pool
    try:
        yield
    finally:
        new_pool.closeall()
        database._connection_pool = old_pool


def _reading(sequence, *, community_kwh="0.040"):
    return {
        "metering_point_id": POINT,
        "direction": "consumption",
        "measured_at": MEASURED_AT + timedelta(minutes=15 * (sequence - 1)),
        "resolution_minutes": 15,
        "total_kwh": Decimal("0.100"),
        "grid_kwh": Decimal("0.060"),
        "community_kwh": Decimal(community_kwh),
        "condition_code": None,
    }


def _setup_schema():
    with database.get_connection() as conn, conn.cursor() as cur:
        cur.execute(METERING_SCHEMA)


@pytest.mark.integration
def test_reading_counts_and_corrections_are_transaction_visible():
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")

    with _temporary_database() as url, _pool_against(url):
        _setup_schema()
        pristine = [_reading(1), _reading(2)]

        created = database.save_metering_point_readings(
            pristine, source_document_id="DOC-1"
        )
        assert created == {
            "written": 2,
            "new": 2,
            "corrected": 0,
            "unchanged": 0,
            "samples": [],
        }
        assert len(database.get_metering_point_readings(POINT)) == 2

        unchanged = database.save_metering_point_readings(
            pristine, source_document_id="DOC-1"
        )
        assert unchanged == {
            "written": 2,
            "new": 0,
            "corrected": 0,
            "unchanged": 2,
            "samples": [],
        }

        corrected_rows = [_reading(1, community_kwh="0.041"), _reading(2)]
        corrected = database.save_metering_point_readings(
            corrected_rows, source_document_id="DOC-1"
        )
        assert corrected == {
            "written": 2,
            "new": 0,
            "corrected": 1,
            "unchanged": 1,
            "samples": [(POINT, "consumption", MEASURED_AT)],
        }

        provenance_corrected = database.save_metering_point_readings(
            corrected_rows, source_document_id="DOC-2"
        )
        assert {
            key: value
            for key, value in provenance_corrected.items()
            if key != "samples"
        } == {
            "written": 2,
            "new": 0,
            "corrected": 2,
            "unchanged": 0,
        }
        assert set(provenance_corrected["samples"]) == {
            (POINT, "consumption", MEASURED_AT),
            (POINT, "consumption", MEASURED_AT + timedelta(minutes=15)),
        }

        stored = database.get_metering_point_readings(POINT)

    assert stored[0]["source_document_id"] == "DOC-2"
    assert stored[1]["source_document_id"] == "DOC-2"
    by_time = {row["measured_at"]: row for row in stored}
    assert by_time[MEASURED_AT]["community_kwh"] == pytest.approx(0.041)
    assert by_time[MEASURED_AT + timedelta(minutes=15)][
        "community_kwh"
    ] == pytest.approx(0.04)


@pytest.mark.integration
def test_sdat_ledger_conflict_updates_audit_fields_without_replacing_identity():
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")

    created_at = datetime(2026, 1, 6, 1, 0, tzinfo=timezone.utc)
    period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    period_end = datetime(2026, 1, 6, tzinfo=timezone.utc)
    original = {
        "document_id": "DOC-1",
        "doc_type": "E66",
        "file_name": "first.xml",
        "vnb_community_id": "VNB-LEG-1",
        "document_created_at": created_at,
        "period_start": period_start,
        "period_end": period_end,
        "block_count": 2,
        "row_count": 6,
        "new_count": 6,
        "corrected_count": 0,
    }

    with _temporary_database() as url, _pool_against(url):
        _setup_schema()
        assert database.record_sdat_import(original) is True
        first = database.get_sdat_import("DOC-1")

        replacement = {
            **original,
            "doc_type": "E99",
            "file_name": "second.xml.gz",
            "vnb_community_id": "VNB-OTHER",
            "block_count": 3,
            "row_count": 9,
            "new_count": 1,
            "corrected_count": 8,
        }
        assert database.record_sdat_import(replacement) is True
        updated = database.get_sdat_import("DOC-1")

    assert updated["id"] == first["id"], "the conflict updates one ledger row"
    assert updated["file_name"] == "second.xml.gz"
    assert updated["block_count"] == 3
    assert updated["row_count"] == 9
    assert updated["new_count"] == 1
    assert updated["corrected_count"] == 8
    assert updated["doc_type"] == "E66"
    assert updated["vnb_community_id"] == "VNB-LEG-1"
    assert updated["document_created_at"] == created_at
    assert updated["period_start"] == period_start
    assert updated["period_end"] == period_end


@pytest.mark.integration
def test_billable_period_snapshot_holds_under_concurrent_assignment(monkeypatch):
    """A mid-read assignment cannot remove a point from both returned sets.

    The reader pauses after reading assigned points and readings. A writer on
    a separate connection assigns the formerly unassigned point and commits
    before the final unassigned query. REPEATABLE READ preserves its earlier
    classification; READ COMMITTED would omit it from both returned sets.
    """
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("needs a live database")

    community_id = "LEG-SNAP-1"
    vnb_community_id = "VNB-LEG-SNAP-1"
    assigned_point = "CH000000000000000000000000000001"
    unassigned_point = "CH000000000000000000000000000002"
    period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    period_end = datetime(2026, 2, 1, tzinfo=timezone.utc)

    with _temporary_database() as url, _pool_against(url):
        _setup_schema()
        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO metering_points
                    (metering_point_id, community_id, vnb_community_id, active)
                VALUES (%s, %s, %s, TRUE), (%s, NULL, %s, TRUE)
                """,
                (
                    assigned_point,
                    community_id,
                    vnb_community_id,
                    unassigned_point,
                    vnb_community_id,
                ),
            )
            cur.execute(
                """
                INSERT INTO sdat_imports (document_id, doc_type, vnb_community_id)
                VALUES ('DOC-ASSIGNED', 'E66', %(vnb)s),
                       ('DOC-UNASSIGNED', 'E66', %(vnb)s)
                """,
                {"vnb": vnb_community_id},
            )
            cur.execute(
                """
                INSERT INTO metering_point_readings
                    (metering_point_id, direction, measured_at,
                     resolution_minutes, total_kwh, grid_kwh, community_kwh,
                     source_document_id)
                VALUES
                    (%(assigned)s, 'consumption', %(at)s, 15,
                     0.1, 0.06, 0.04, 'DOC-ASSIGNED'),
                    (%(unassigned)s, 'consumption', %(at)s, 15,
                     0.1, 0.06, 0.04, 'DOC-UNASSIGNED')
                """,
                {
                    "assigned": assigned_point,
                    "unassigned": unassigned_point,
                    "at": MEASURED_AT,
                },
            )

        readings_completed = threading.Event()
        release_reader = threading.Event()
        writer_committed = threading.Event()
        writer_errors = []

        real_get_connection = database.get_connection

        class _PausingCursor:
            def __init__(self, cursor):
                self._cursor = cursor
                self._select_count = 0

            def execute(self, sql, params=None):
                self._cursor.execute(sql, params)
                if sql.lstrip().upper().startswith("SELECT"):
                    self._select_count += 1
                if self._select_count == 2:
                    # Points and readings are fixed. Commit the assignment
                    # before the final unassigned-point query.
                    readings_completed.set()
                    if not release_reader.wait(timeout=30):
                        raise RuntimeError("writer did not release the reader")

            def __getattr__(self, name):
                return getattr(self._cursor, name)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return self._cursor.__exit__(exc_type, exc, tb)

        class _PausingConnection:
            def __init__(self, conn):
                self._conn = conn

            def cursor(self):
                return _PausingCursor(self._conn.cursor())

            def __getattr__(self, name):
                return getattr(self._conn, name)

        @contextmanager
        def _pausing_get_connection():
            with real_get_connection() as conn:
                yield _PausingConnection(conn)

        def _assign_point_concurrently():
            try:
                if not readings_completed.wait(timeout=30):
                    raise RuntimeError("reader never completed its readings query")
                writer = psycopg2.connect(url)
                try:
                    with writer.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE metering_points
                            SET community_id = %s
                            WHERE metering_point_id = %s
                            """,
                            (community_id, unassigned_point),
                        )
                    writer.commit()
                finally:
                    writer.close()
                writer_committed.set()
            except Exception as e:
                writer_errors.append(e)
            finally:
                release_reader.set()

        monkeypatch.setattr(store.metering, "_get_connection", _pausing_get_connection)
        thread = threading.Thread(target=_assign_point_concurrently)
        thread.start()
        try:
            snapshot = database.get_billable_period_snapshot(
                community_id, period_start, period_end
            )
        finally:
            release_reader.set()
            thread.join(timeout=30)

        assert not thread.is_alive()
        assert writer_committed.is_set(), f"writer failed: {writer_errors}"
        assert not writer_errors

        represented_points = {
            row["metering_point_id"] for row in snapshot["readings"]
        } | set(snapshot["unassigned_point_ids"])
        assert unassigned_point in represented_points
        # Sanity: the seeded period data was actually visible to the reader.
        assert assigned_point in {
            row["metering_point_id"] for row in snapshot["readings"]
        }
