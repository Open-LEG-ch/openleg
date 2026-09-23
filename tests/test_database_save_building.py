# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract tests for save_building parameter binding."""

from contextlib import contextmanager

import database


class _FakeCursor:
    def __init__(self):
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _connection_factory(cursor):
    @contextmanager
    def _factory():
        yield _FakeConnection(cursor)

    return _factory


def test_save_building_keeps_a_new_registration_unverified(monkeypatch):
    cursor = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _connection_factory(cursor))

    database.save_building(
        building_id="b1",
        email="a@b.ch",
        profile={"address": "Musterweg 1"},
        consents={"share_with_neighbors": True},
    )

    query, params = cursor.executed[0]
    normalized = " ".join(query.split())

    assert "registered_at, verified, verified_at, user_type" in normalized
    assert "to_timestamp(%s), %s, to_timestamp(%s), %s" in normalized
    assert normalized.split("VALUES (", 1)[1].split(") ON CONFLICT", 1)[0].count(
        "%s"
    ) == len(params)
    assert params[11] is False
    assert params[12] is None
    assert "WHEN buildings.verified THEN buildings.email" in normalized
    assert "verified = buildings.verified OR EXCLUDED.verified" in normalized
    assert "COALESCE( buildings.verified_at, EXCLUDED.verified_at )" in normalized


def test_save_building_persists_verification_token_in_the_same_transaction(
    monkeypatch,
):
    cursor = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _connection_factory(cursor))

    assert database.save_building(
        building_id="b1",
        email="a@b.ch",
        profile={"address": "Musterweg 1"},
        consents={},
        verification_token="token-1",
        verification_ttl_seconds=60,
    )

    token_query, token_params = cursor.executed[-1]
    assert "INSERT INTO tokens" in token_query
    assert token_params == ("token-1", "b1", 60)
