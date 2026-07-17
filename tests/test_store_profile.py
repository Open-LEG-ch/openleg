# SPDX-License-Identifier: AGPL-3.0-or-later
"""Interface tests for the municipality profile data repository (store.profile).

Verifies the extracted module resolves the connection seam via
`database.get_connection` and that `database` re-exports the identical objects,
so legacy callers and existing monkeypatches keep working unchanged. Mirrors
`test_store_ranking.py`; the seam is the test surface (no Flask, no live pool).
"""

from contextlib import contextmanager
import subprocess
import sys

import database
from store import profile


_REEXPORTED = (
    "save_elcom_tariffs",
    "get_elcom_tariffs",
    "save_municipality_profile",
    "get_municipality_profile",
    "get_all_municipality_profiles",
    "get_all_municipality_profile_bfs_numbers",
    "get_profile_bfs_missing_elcom_tariffs",
    "save_sonnendach_municipal",
    "get_sonnendach_municipal",
    "search_municipality_profiles",
)


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
    for name in _REEXPORTED:
        assert getattr(database, name) is getattr(profile, name), name


def test_store_profile_imports_without_database_bootstrap():
    result = subprocess.run(
        [sys.executable, "-c", "import store.profile; print('ok')"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_profile_uses_database_connection_seam(monkeypatch):
    # Monkeypatching database.get_connection must affect store.profile calls,
    # proving the seam is shared (not a stale direct import binding).
    cur = _FakeCursor(one={"bfs_number": 4021, "name": "Baden"})
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    row = profile.get_municipality_profile(4021)
    assert row == {"bfs_number": 4021, "name": "Baden"}
    assert "FROM municipality_profiles" in cur.executed[0][0]
    assert cur.executed[0][1] == (4021,)


def test_get_all_municipality_profiles_filters_by_kanton(monkeypatch):
    cur = _FakeCursor(rows=[])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    profile.get_all_municipality_profiles(kanton="AG")
    query, params = cur.executed[0]
    assert "kanton = %s" in query
    assert params == ("AG",)


def test_save_elcom_tariffs_empty_returns_zero():
    assert profile.save_elcom_tariffs([]) == 0


def test_search_municipality_profiles_matches_name_case_insensitive(monkeypatch):
    cur = _FakeCursor(rows=[{"bfs_number": 4021, "name": "Baden"}])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    rows = profile.search_municipality_profiles("bad")
    assert rows == [{"bfs_number": 4021, "name": "Baden"}]
    query, params = cur.executed[0]
    assert "municipality_profiles" in query
    assert "ILIKE" in query
    assert params[0] == "%bad%"


def test_search_municipality_profiles_blank_query_returns_empty(monkeypatch):
    cur = _FakeCursor(rows=[{"bfs_number": 1, "name": "X"}])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert profile.search_municipality_profiles("   ") == []
    assert cur.executed == []
