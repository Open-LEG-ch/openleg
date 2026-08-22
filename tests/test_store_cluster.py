# SPDX-License-Identifier: AGPL-3.0-or-later
"""Interface tests for the cluster repository module (store.cluster).

A cluster is the provisional grouping the clustering task writes back for a set
of neighbouring buildings. The module depends on buildings and on nothing else.
"""

import subprocess
import sys
from contextlib import contextmanager

import database
from store import cluster

REEXPORTED = ("save_cluster", "save_cluster_info")


class _FakeCursor:
    def __init__(self, rowcount=1):
        self.rowcount = rowcount
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

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
    for name in REEXPORTED:
        assert getattr(database, name) is getattr(cluster, name), name


def test_store_cluster_imports_without_database_bootstrap():
    result = subprocess.run(
        [sys.executable, "-c", "import store.cluster; print('ok')"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_save_cluster_uses_the_database_connection_seam(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert cluster.save_cluster("b1", 3) is True
    query, params = cur.executed[0]
    assert "clusters" in query.lower()
    assert "b1" in params
    assert 3 in params


def test_save_cluster_info_persists_the_community_summary(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert cluster.save_cluster_info(3, {"autarky_percent": 42.0}) is True
    query, _params = cur.executed[0]
    assert "cluster" in query.lower()


def test_a_broken_connection_never_leaks_the_exception(monkeypatch):
    @contextmanager
    def _broken():
        raise RuntimeError("db down")
        yield

    monkeypatch.setattr(database, "get_connection", _broken)

    assert cluster.save_cluster("b1", 3) is False
    assert cluster.save_cluster_info(3, {}) is False
