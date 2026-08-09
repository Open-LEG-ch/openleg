# SPDX-License-Identifier: AGPL-3.0-or-later
"""Interface tests for the meter-data repository module (store.meter).

Meter data is the LEG's smart-meter CSV state. This module owns its persistence
and resolves the connection seam via ``database.get_connection`` at call time, so
existing monkeypatches keep working and ``database`` re-exports the identical
objects for legacy callers (``import database as db; db.get_meter_readings()``).
"""

import subprocess
import sys
from contextlib import contextmanager

import database
from store import meter


class _FakeCursor:
    def __init__(self, rows=None, one=None):
        self.rows = rows or []
        self.one = one
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.one

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _conn_ctx(cursor):
    @contextmanager
    def _factory():
        yield _FakeConnection(cursor)

    return _factory


def test_database_reexports_are_identical_objects():
    # The re-export shim must expose the exact same function objects.
    assert database.save_meter_readings is meter.save_meter_readings
    assert database.get_meter_readings is meter.get_meter_readings
    assert database.get_meter_reading_stats is meter.get_meter_reading_stats


def test_store_meter_imports_without_database_bootstrap():
    result = subprocess.run(
        [sys.executable, "-c", "import store.meter; print('ok')"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_get_meter_readings_uses_database_connection_seam(monkeypatch):
    # Monkeypatching database.get_connection must affect store.meter calls,
    # proving the seam is shared (not a stale direct import binding).
    cur = _FakeCursor(rows=[{"building_id": "b1", "consumption_kwh": 4.2}])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    rows = meter.get_meter_readings("b1")
    assert rows == [{"building_id": "b1", "consumption_kwh": 4.2}]
    query, params = cur.executed[0]
    assert "FROM meter_readings" in query
    assert params[0] == "b1"
    assert params[-1] == 1000  # default limit


def test_get_meter_readings_applies_time_window(monkeypatch):
    cur = _FakeCursor(rows=[])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    meter.get_meter_readings("b1", start="2026-01-01", end="2026-02-01", limit=50)
    query, params = cur.executed[0]
    assert "timestamp >= %s" in query
    assert "timestamp <= %s" in query
    assert params == ["b1", "2026-01-01", "2026-02-01", 50]


def test_get_meter_reading_stats_empty_returns_empty_dict(monkeypatch):
    cur = _FakeCursor(one=None)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert meter.get_meter_reading_stats("b1") == {}
