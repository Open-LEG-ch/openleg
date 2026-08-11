# SPDX-License-Identifier: AGPL-3.0-or-later
"""Interface tests for the LEG community billing repository (store.billing).

Verifies the extracted module resolves the connection seam via
`database.get_connection` and that `database` re-exports the identical objects,
so legacy callers and existing monkeypatches keep working unchanged. Mirrors
`test_store_ranking.py` / `test_store_profile.py`; the seam is the test surface.
"""

import subprocess
import sys
from contextlib import contextmanager

import database
from store import billing

_REEXPORTED = (
    "save_billing_period",
    "get_active_communities",
    "get_community_for_building",
    "get_billing_period",
    "get_billing_period_for_window",
    "get_billing_policy",
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
        assert getattr(database, name) is getattr(billing, name), name


def test_store_billing_imports_without_database_bootstrap():
    result = subprocess.run(
        [sys.executable, "-c", "import store.billing; print('ok')"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_billing_uses_database_connection_seam(monkeypatch):
    # Monkeypatching database.get_connection must affect store.billing calls,
    # proving the seam is shared (not a stale direct import binding).
    cur = _FakeCursor(rows=[{"community_id": 1, "status": "active"}])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    rows = billing.get_active_communities()
    assert rows == [{"community_id": 1, "status": "active"}]
    assert "communities" in cur.executed[0][0]
    assert "status = 'active'" in cur.executed[0][0]


def test_get_billing_period_missing_returns_none(monkeypatch):
    cur = _FakeCursor(one=None)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert billing.get_billing_period(999) is None


def test_get_effective_policy_and_existing_period_use_the_connection_seam(monkeypatch):
    policy = {
        "tariff_id": 7,
        "internal_price_chf_per_kwh": 0.12,
        "grid_fee_chf_per_kwh": 0.08,
        "network_level": "same",
        "distribution_model": "proportional",
    }
    policy_cur = _FakeCursor(one=policy)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(policy_cur))

    assert (
        billing.get_billing_policy("community-a", "2026-01-01", "2026-02-01") == policy
    )
    period_cur = _FakeCursor(one={"id": 42, "input_fingerprint": "abc"})
    monkeypatch.setattr(database, "get_connection", _conn_ctx(period_cur))
    assert billing.get_billing_period_for_window(
        "community-a", "2026-01-01", "2026-02-01"
    ) == {"id": 42, "input_fingerprint": "abc"}
    assert "billing_tariffs" in policy_cur.executed[0][0]
    assert policy_cur.executed[0][1] == (
        "community-a",
        "2026-01-01",
        "2026-02-01",
    )
    assert "period_start" in period_cur.executed[0][0]
