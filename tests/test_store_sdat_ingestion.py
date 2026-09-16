# SPDX-License-Identifier: AGPL-3.0-or-later
"""Persistence contracts for scheduled SDAT ingestion."""

from contextlib import contextmanager
from datetime import time

from store import sdat_ingestion


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def test_advisory_lock_uses_tenant_key_and_same_session_for_release(monkeypatch):
    cursor = Cursor([{"acquired": True}])
    connection = Connection(cursor)

    connection_requests = []

    @contextmanager
    def get_connection():
        connection_requests.append(True)
        yield connection

    monkeypatch.setattr(sdat_ingestion, "_get_connection", get_connection)
    assert sdat_ingestion.acquire_sdat_ingestion_lock("dietikon") is True
    sdat_ingestion.release_sdat_ingestion_lock("dietikon")

    assert "pg_try_advisory_lock" in cursor.executed[0][0]
    assert cursor.executed[0][1] == ("sdat:dietikon",)
    assert "pg_advisory_unlock" in cursor.executed[1][0]
    assert len(connection_requests) == 1


def test_schedule_read_exposes_both_recovery_timestamps(monkeypatch):
    cursor = Cursor(
        [
            {
                "territory": "dietikon",
                "local_time": time(2, 30),
                "last_success_at": "2026-09-14T02:31:00Z",
                "last_failure_at": "2026-09-15T02:31:00Z",
            }
        ]
    )

    @contextmanager
    def get_connection():
        yield Connection(cursor)

    monkeypatch.setattr(sdat_ingestion, "_get_connection", get_connection)
    result = sdat_ingestion.list_sdat_ingestion_schedules()

    assert result[0]["local_time"] == "02:30"
    assert result[0]["last_success_at"] == "2026-09-14T02:31:00Z"
    assert result[0]["last_failure_at"] == "2026-09-15T02:31:00Z"
    query = cursor.executed[0][0]
    assert "last_success_at" in query
    assert "last_failure_at" in query
