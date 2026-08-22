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


def test_save_building_binds_verified_at_via_to_timestamp(monkeypatch):
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
    assert isinstance(params[12], (int, float))
