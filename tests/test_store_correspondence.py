# SPDX-License-Identifier: AGPL-3.0-or-later
"""Interface tests for the correspondence ledger repository (store.correspondence).

Phase 6 MVP of docs/leg-registry.md: one shared log per community of
outgoing and incoming mail (email and physical post), manually logged.
No external mail provider involved. Mirrors test_store_registry.py; the
seam is the test surface.
"""

from contextlib import contextmanager
import subprocess
import sys

import database
from store import correspondence


_REEXPORTED = (
    "log_correspondence",
    "list_correspondence",
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
        assert getattr(database, name) is getattr(correspondence, name), name


def test_store_correspondence_imports_without_database_bootstrap():
    result = subprocess.run(
        [sys.executable, "-c", "import store.correspondence; print('ok')"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_log_correspondence_inserts_row(monkeypatch):
    cur = _FakeCursor(one={"id": 3})
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    entry_id = correspondence.log_correspondence(
        community_id="c0ffee",
        direction="out",
        channel="post",
        counterparty="Regionalwerke Baden AG",
        subject="LEG Anmeldung",
        notes="Per Einschreiben versendet",
        logged_by="b-admin",
    )
    assert entry_id == 3
    query, params = cur.executed[0]
    assert "correspondence_log" in query
    assert "c0ffee" in params
    assert "out" in params
    assert "post" in params


def test_log_correspondence_rejects_bad_direction_or_channel(monkeypatch):
    cur = _FakeCursor(one={"id": 3})
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert (
        correspondence.log_correspondence(
            "c0ffee", "sideways", "post", "X", "Y", "", "b-admin"
        )
        is None
    )
    assert (
        correspondence.log_correspondence(
            "c0ffee", "out", "fax", "X", "Y", "", "b-admin"
        )
        is None
    )
    assert cur.executed == []


def test_list_correspondence_scoped_to_community(monkeypatch):
    cur = _FakeCursor(rows=[{"id": 1, "community_id": "c0ffee"}])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    rows = correspondence.list_correspondence("c0ffee")
    assert rows == [{"id": 1, "community_id": "c0ffee"}]
    query, params = cur.executed[0]
    assert "community_id = %s" in query
    assert params[0] == "c0ffee"
