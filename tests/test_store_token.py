# SPDX-License-Identifier: AGPL-3.0-or-later
"""Interface tests for the token repository (store.token).

Verifies the extracted module resolves the connection seam via
`database.get_connection` and that `database` re-exports the identical
objects, so legacy callers and existing monkeypatches keep working
unchanged. Mirrors `test_store_email_queue.py`; the seam is the test
surface.
"""

import subprocess
import sys
from contextlib import contextmanager

import database
from store import token as store_token

_REEXPORTED = (
    "save_token",
    "get_token",
    "use_token",
    "delete_tokens_for_building",
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
        assert getattr(database, name) is getattr(store_token, name), name


def test_store_token_imports_without_database_bootstrap():
    result = subprocess.run(
        [sys.executable, "-c", "import store.token; print('ok')"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_get_token_uses_database_connection_seam(monkeypatch):
    # Monkeypatching database.get_connection must affect store.token calls,
    # proving the seam is shared (not a stale direct import binding).
    cur = _FakeCursor(one={"token": "t1", "building_id": "b1"})
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    info = store_token.get_token("t1")
    assert info == {"token": "t1", "building_id": "b1"}
    assert "FROM tokens" in cur.executed[0][0]
    assert cur.executed[0][1] == ("t1",)


def test_use_token_reports_rowcount(monkeypatch):
    cur = _FakeCursor(rowcount=1)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    assert store_token.use_token("t1") is True

    cur = _FakeCursor(rowcount=0)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    assert store_token.use_token("missing") is False
    query, params = cur.executed[0]
    assert "used_at IS NULL" in query
    assert "expires_at > CURRENT_TIMESTAMP" in query
    assert params == ("missing",)


def test_delete_tokens_filters_by_type(monkeypatch):
    cur = _FakeCursor(rowcount=2)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert store_token.delete_tokens_for_building("b1", "unsubscribe") == 2
    query, params = cur.executed[0]
    assert "token_type" in query
    assert params == ("b1", "unsubscribe")


def test_save_token_wires_params(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert store_token.save_token("t1", "b1", "verification", ttl_seconds=3600) is True
    query, params = cur.executed[0]
    assert "INSERT INTO tokens" in query
    assert "(%s * INTERVAL '1 second')" in query
    assert "INTERVAL '%s seconds'" not in query
    assert params == ("t1", "b1", "verification", 3600)
