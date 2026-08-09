# SPDX-License-Identifier: AGPL-3.0-or-later
"""Interface tests for the LEG registry repository (store.registry).

Verifies the extracted module resolves the connection seam via
`database.get_connection` and that `database` re-exports the identical objects,
so legacy callers and existing monkeypatches keep working unchanged. Mirrors
`test_store_utility.py`; the seam is the test surface.
"""

import subprocess
import sys
from contextlib import contextmanager

import database
from store import registry

_REEXPORTED = (
    "save_registry_entry",
    "get_registry_entry",
    "get_registry_entry_by_slug",
    "list_registry_entries",
    "update_registry_entry_moderation",
    "get_registry_pending_count",
    "set_registry_claim_token",
    "get_registry_entry_by_claim_token",
    "mark_registry_entry_claimed",
    "set_registry_verification_token",
    "get_registry_entry_by_verification_token",
    "mark_registry_entry_verified",
    "get_registry_entries_needing_verification",
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
        assert getattr(database, name) is getattr(registry, name), name


def test_store_registry_imports_without_database_bootstrap():
    result = subprocess.run(
        [sys.executable, "-c", "import store.registry; print('ok')"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_registry_uses_database_connection_seam(monkeypatch):
    cur = _FakeCursor(
        rows=[{"id": 1, "slug": "leg-baden", "moderation_status": "published"}]
    )
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    rows = registry.list_registry_entries(moderation_status="published")
    assert rows == [{"id": 1, "slug": "leg-baden", "moderation_status": "published"}]
    assert "leg_registry" in cur.executed[0][0]
    assert "moderation_status = %s" in cur.executed[0][0]


def test_list_registry_entries_always_includes_moderation_status_param(monkeypatch):
    # A caller must never be able to omit the moderation filter and see
    # everything; the function signature defaults it to 'published'.
    cur = _FakeCursor(rows=[])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    registry.list_registry_entries()
    assert cur.executed[0][1][0] == "published"


def test_get_registry_entry_by_slug_missing_returns_none(monkeypatch):
    cur = _FakeCursor(one=None)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert registry.get_registry_entry_by_slug("nope") is None


def test_save_registry_entry_defaults_to_pending_moderation(monkeypatch):
    cur = _FakeCursor(one={"id": 5})
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    registry.save_registry_entry(
        slug="leg-baden",
        name="LEG Baden",
        contact_email="info@example.ch",
    )
    query, params = cur.executed[0]
    assert "leg_registry" in query
    assert "pending" in params
    assert "self_submitted" in params


def test_set_registry_claim_token_updates_expiry(monkeypatch):
    cur = _FakeCursor(rowcount=1)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert registry.set_registry_claim_token(5, "tok123", ttl_seconds=3600) is True
    assert "claim_token" in cur.executed[0][0]


def test_get_registry_pending_count_returns_zero_by_default(monkeypatch):
    cur = _FakeCursor(one={"count": 0})
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert registry.get_registry_pending_count() == 0


def test_set_registry_verification_token_updates_expiry(monkeypatch):
    cur = _FakeCursor(rowcount=1)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert (
        registry.set_registry_verification_token(5, "vtok123", ttl_seconds=3600) is True
    )
    assert "verification_token" in cur.executed[0][0]


def test_get_registry_entry_by_verification_token_missing_returns_none(monkeypatch):
    cur = _FakeCursor(one=None)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert registry.get_registry_entry_by_verification_token("nope") is None


def test_mark_registry_entry_verified_clears_token(monkeypatch):
    cur = _FakeCursor(rowcount=1)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert registry.mark_registry_entry_verified(5) is True
    assert "last_verified_at" in cur.executed[0][0]
    assert "verification_token = NULL" in cur.executed[0][0]


def test_get_registry_entries_needing_verification_defaults_published_only(
    monkeypatch,
):
    cur = _FakeCursor(rows=[])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    registry.get_registry_entries_needing_verification(stale_days=90)
    query = cur.executed[0][0]
    assert "leg_registry" in query
    assert "moderation_status = 'published'" in query
