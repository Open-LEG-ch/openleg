# SPDX-License-Identifier: AGPL-3.0-or-later
"""Interface tests for the municipality lookup repository (store.municipality).

Verifies the extracted module resolves the connection seam via
`database.get_connection` and that `database` re-exports the identical objects,
so legacy callers and existing monkeypatches keep working unchanged. Mirrors
`test_store_profile.py` / `test_store_utility.py`; the seam is the test surface.
"""

import subprocess
import sys
from contextlib import contextmanager

import database
from store import municipality

_REEXPORTED = (
    "get_municipality",
    "get_municipality_by_admin_email",
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
    for name in _REEXPORTED:
        assert getattr(database, name) is getattr(municipality, name), name


def test_store_municipality_imports_without_database_bootstrap():
    result = subprocess.run(
        [sys.executable, "-c", "import store.municipality; print('ok')"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_get_municipality_uses_database_connection_seam_for_id(monkeypatch):
    cur = _FakeCursor(one={"id": 7, "name": "Baden"})
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    row = municipality.get_municipality(municipality_id=7)
    assert row == {"id": 7, "name": "Baden"}
    query, params = cur.executed[0]
    assert "municipalities" in query
    assert "id = %s" in query
    assert params == (7,)


def test_get_municipality_uses_database_connection_seam_for_bfs(monkeypatch):
    cur = _FakeCursor(one={"bfs_number": 4021, "name": "Baden"})
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    row = municipality.get_municipality(bfs_number=4021)
    assert row == {"bfs_number": 4021, "name": "Baden"}
    query, params = cur.executed[0]
    assert "bfs_number = %s" in query
    assert params == (4021,)


def test_get_municipality_uses_database_connection_seam_for_subdomain(monkeypatch):
    cur = _FakeCursor(one={"subdomain": "baden", "name": "Baden"})
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    row = municipality.get_municipality(subdomain="baden")
    assert row == {"subdomain": "baden", "name": "Baden"}
    query, params = cur.executed[0]
    assert "subdomain = %s" in query
    assert params == ("baden",)


def test_get_municipality_with_no_args_returns_none():
    assert municipality.get_municipality() is None


def test_get_municipality_by_admin_email_uses_database_connection_seam(monkeypatch):
    cur = _FakeCursor(one={"id": 7, "admin_email": "admin@baden.ch"})
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    row = municipality.get_municipality_by_admin_email("Admin@Baden.CH")
    assert row == {"id": 7, "admin_email": "admin@baden.ch"}
    query, params = cur.executed[0]
    assert "municipalities" in query
    assert "LOWER(admin_email) = LOWER(%s)" in query
    assert params == ("Admin@Baden.CH",)


def test_get_municipality_missing_returns_none(monkeypatch):
    cur = _FakeCursor(one=None)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert municipality.get_municipality(municipality_id=99) is None


def test_get_municipality_by_admin_email_missing_returns_none(monkeypatch):
    cur = _FakeCursor(one=None)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert municipality.get_municipality_by_admin_email("nobody@example.ch") is None
