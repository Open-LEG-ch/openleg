# SPDX-License-Identifier: AGPL-3.0-or-later
"""Interface tests for API-client persistence (store.api_client)."""

import subprocess
import sys
from contextlib import contextmanager

import database
from store import api_client

_REEXPORTED = (
    "save_api_client",
    "get_api_client_by_key",
    "track_api_usage",
    "get_api_usage_count",
)


class _FakeCursor:
    def __init__(self, one=None):
        self.one = one
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

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
    for name in _REEXPORTED:
        assert getattr(database, name) is getattr(api_client, name), name


def test_store_api_client_imports_without_database_bootstrap():
    result = subprocess.run(
        [sys.executable, "-c", "import store.api_client; print('ok')"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_lookup_uses_database_connection_seam(monkeypatch):
    cur = _FakeCursor(one={"id": 7, "company_name": "OpenLEG"})
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert api_client.get_api_client_by_key("hash") == {
        "id": 7,
        "company_name": "OpenLEG",
    }
    query, params = cur.executed[0]
    assert "active = TRUE" in query
    assert params == ("hash",)


def test_usage_window_is_parameterized(monkeypatch):
    cur = _FakeCursor(one={"count": 3})
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert api_client.get_api_usage_count(7, hours=24) == 3
    query, params = cur.executed[0]
    assert "INTERVAL '1 hour' * %s" in query
    assert "'%s hours'" not in query
    assert params == (7, 24)


def test_usage_window_rejects_invalid_values(monkeypatch):
    cur = _FakeCursor(one={"count": 3})
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert api_client.get_api_usage_count(7, hours=0) == 0
    assert api_client.get_api_usage_count(7, hours=True) == 0
    assert cur.executed == []
