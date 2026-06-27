# SPDX-License-Identifier: AGPL-3.0-or-later
"""Interface tests for the EVU/VNB utility-client repository (store.utility).

Verifies the extracted module resolves the connection seam via
`database.get_connection` and that `database` re-exports the identical objects,
so legacy callers and existing monkeypatches keep working unchanged. Mirrors
`test_store_ranking.py` / `test_store_profile.py`; the seam is the test surface.
"""

from contextlib import contextmanager
import subprocess
import sys

import database
from store import utility


_REEXPORTED = (
    "save_utility_client",
    "get_utility_client",
    "get_utility_client_by_email",
    "get_utility_client_by_magic_token",
    "set_utility_magic_token",
    "clear_utility_magic_token",
    "update_utility_client_status",
    "update_utility_client_api_key",
    "get_all_utility_clients",
    "get_utility_client_stats",
)


class _FakeCursor:
    def __init__(self, rows=None, one=None, rowcount=0):
        self.rows = rows or []
        self.one = one
        self.rowcount = rowcount
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
        assert getattr(database, name) is getattr(utility, name), name


def test_store_utility_imports_without_database_bootstrap():
    result = subprocess.run(
        [sys.executable, "-c", "import store.utility; print('ok')"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_utility_uses_database_connection_seam(monkeypatch):
    # Monkeypatching database.get_connection must affect store.utility calls,
    # proving the seam is shared (not a stale direct import binding).
    cur = _FakeCursor(rows=[{"client_id": "c1", "status": "active"}])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    rows = utility.get_all_utility_clients(status="active")
    assert rows == [{"client_id": "c1", "status": "active"}]
    assert "utility_clients" in cur.executed[0][0]
    assert "status = %s" in cur.executed[0][0]
    assert cur.executed[0][1] == ("active",)


def test_get_utility_client_missing_returns_none(monkeypatch):
    cur = _FakeCursor(one=None)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert utility.get_utility_client("nope") is None
