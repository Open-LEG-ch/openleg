# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contracts for hashed, single-use dashboard access tokens."""

from contextlib import contextmanager

import database
from store import dashboard_access


class _FakeCursor:
    def __init__(self, *, one=None, rowcount=0):
        self.one = one
        self.rowcount = rowcount
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

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


def test_database_reexports_dashboard_access_repository():
    for name in (
        "save_dashboard_access_token",
        "consume_dashboard_access_token",
        "revoke_dashboard_access_tokens",
    ):
        assert getattr(database, name) is getattr(dashboard_access, name)


def test_save_persists_only_the_hash_and_expiry(monkeypatch):
    cursor = _FakeCursor(rowcount=1)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cursor))

    assert dashboard_access.save_dashboard_access_token(
        "a" * 64, "building-1", ttl_seconds=1800
    )

    query, params = cursor.executed[0]
    normalized = " ".join(query.split())
    assert "INSERT INTO dashboard_access_tokens" in query
    assert "token_hash" in query
    assert "%s * INTERVAL '1 second'" in normalized
    assert params == ("a" * 64, "building-1", 1800)


def test_save_fails_closed_on_impossible_hash_collision(monkeypatch):
    cursor = _FakeCursor(rowcount=0)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cursor))

    assert not dashboard_access.save_dashboard_access_token(
        "a" * 64, "building-1", ttl_seconds=1800
    )

    query, _params = cursor.executed[0]
    normalized = " ".join(query.split())
    assert "ON CONFLICT (token_hash) DO NOTHING" in normalized
    assert "DO UPDATE" not in normalized


def test_consume_is_atomic_and_rejects_expired_used_or_revoked_tokens(monkeypatch):
    cursor = _FakeCursor(one={"building_id": "building-1"})
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cursor))

    result = dashboard_access.consume_dashboard_access_token("a" * 64)

    assert result == {"building_id": "building-1"}
    query, params = cursor.executed[0]
    normalized = " ".join(query.split())
    assert normalized.startswith("UPDATE dashboard_access_tokens")
    assert "SET used_at = CURRENT_TIMESTAMP" in normalized
    assert "expires_at > CURRENT_TIMESTAMP" in normalized
    assert "used_at IS NULL" in normalized
    assert "revoked_at IS NULL" in normalized
    assert "RETURNING building_id" in normalized
    assert params == ("a" * 64,)


def test_revoke_invalidates_all_unused_tokens_for_building(monkeypatch):
    cursor = _FakeCursor(rowcount=2)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cursor))

    assert dashboard_access.revoke_dashboard_access_tokens("building-1") == 2

    query, params = cursor.executed[0]
    assert "SET revoked_at = CURRENT_TIMESTAMP" in query
    assert "used_at IS NULL" in query
    assert params == ("building-1",)
