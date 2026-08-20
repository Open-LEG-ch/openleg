# SPDX-License-Identifier: AGPL-3.0-or-later
"""PostgreSQL contracts for municipality access tokens."""

from contextlib import contextmanager

import database
from store import municipality_access


class _Cursor:
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


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _connection(cursor):
    @contextmanager
    def factory():
        yield _Connection(cursor)

    return factory


def test_database_reexports_municipality_access_adapter():
    for name in (
        "save_municipality_access_token",
        "consume_municipality_access_token",
        "revoke_municipality_access_tokens",
    ):
        assert getattr(database, name) is getattr(municipality_access, name)


def test_consume_is_atomic_and_rejects_expired_used_or_revoked_tokens(monkeypatch):
    cursor = _Cursor(one={"municipality_id": 7})
    monkeypatch.setattr(database, "get_connection", _connection(cursor))

    assert municipality_access.consume_municipality_access_token("a" * 64) == {
        "municipality_id": 7
    }

    query, params = cursor.executed[0]
    normalized = " ".join(query.split())
    assert normalized.startswith("UPDATE municipality_access_tokens")
    assert "SET used_at = CURRENT_TIMESTAMP" in normalized
    assert "expires_at > CURRENT_TIMESTAMP" in normalized
    assert "used_at IS NULL" in normalized
    assert "revoked_at IS NULL" in normalized
    assert "RETURNING municipality_id" in normalized
    assert params == ("a" * 64,)
