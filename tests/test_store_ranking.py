# SPDX-License-Identifier: AGPL-3.0-or-later
"""Interface tests for the PV/ranking repository module (store.ranking).

Verifies the extracted module resolves the connection seam via
`database.get_connection` and that `database` re-exports the identical objects,
so legacy callers and existing monkeypatches keep working unchanged.
"""

from contextlib import contextmanager
import subprocess
import sys

import database
from store import ranking


class _FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchall(self):
        return self.rows

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
    assert database.upsert_municipality_pv is ranking.upsert_municipality_pv
    assert database.save_municipality_pv_panel is ranking.save_municipality_pv_panel
    assert database.get_pv_profiles is ranking.get_pv_profiles
    assert database.get_pv_movers is ranking.get_pv_movers
    assert database.get_municipality_pv_panel is ranking.get_municipality_pv_panel


def test_store_ranking_imports_without_database_bootstrap():
    result = subprocess.run(
        [sys.executable, "-c", "import store.ranking; print('ok')"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_ranking_uses_database_connection_seam(monkeypatch):
    # Monkeypatching database.get_connection must affect store.ranking calls,
    # proving the seam is shared (not a stale direct import binding).
    cur = _FakeCursor(rows=[{"bfs_number": 4021, "pv_score_pct": 12.5}])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    rows = ranking.get_pv_profiles()
    assert rows == [{"bfs_number": 4021, "pv_score_pct": 12.5}]
    assert "pv_score_pct IS NOT NULL" in cur.executed[0][0]


def test_get_pv_profiles_filters_by_kanton(monkeypatch):
    cur = _FakeCursor(rows=[])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    ranking.get_pv_profiles(kanton="AG")
    query, params = cur.executed[0]
    assert "kanton = %s" in query
    assert params == ("AG",)


def test_save_municipality_pv_panel_empty_returns_zero():
    assert ranking.save_municipality_pv_panel([]) == 0
