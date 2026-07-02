# SPDX-License-Identifier: AGPL-3.0-or-later
"""Interface tests for the tenant repository module (store.tenant).

A Tenant maps a territory (`<territory>.openleg.ch`) to its white-label config.
This module owns that persistence and resolves the connection seam via
``database.get_connection`` at call time, so monkeypatches keep working and
``database`` re-exports the identical objects for legacy callers.
"""

from contextlib import contextmanager
import subprocess
import sys

import database
from store import tenant


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
    assert database.get_tenant_by_territory is tenant.get_tenant_by_territory
    assert database.get_all_active_tenants is tenant.get_all_active_tenants
    assert database.upsert_tenant is tenant.upsert_tenant
    assert database.seed_default_tenant is tenant.seed_default_tenant


def test_store_tenant_imports_without_database_bootstrap():
    result = subprocess.run(
        [sys.executable, "-c", "import store.tenant; print('ok')"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_get_tenant_by_territory_uses_seam(monkeypatch):
    cur = _FakeCursor(one={"territory": "zurich", "active": True})
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    row = tenant.get_tenant_by_territory("zurich")
    assert row == {"territory": "zurich", "active": True}
    query, params = cur.executed[0]
    assert "white_label_configs" in query
    assert params == ("zurich",)


def test_get_all_active_tenants_orders_by_territory(monkeypatch):
    cur = _FakeCursor(rows=[{"territory": "aarau"}, {"territory": "zurich"}])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    rows = tenant.get_all_active_tenants()
    assert rows == [{"territory": "aarau"}, {"territory": "zurich"}]
    assert "ORDER BY territory" in cur.executed[0][0]


def test_seed_default_tenant_is_idempotent_when_present(monkeypatch):
    # An existing zurich tenant short-circuits: no upsert, returns True.
    cur = _FakeCursor(one={"territory": "zurich"})
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    called = {"upsert": False}

    def _fail_upsert(*_a, **_k):
        called["upsert"] = True
        return False

    monkeypatch.setattr(tenant, "upsert_tenant", _fail_upsert)

    assert tenant.seed_default_tenant() is True
    assert called["upsert"] is False
