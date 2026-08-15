# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for organic-growth DB helper functions."""

from contextlib import contextmanager

import database


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, _query, _params=None):
        return None

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return _FakeCursor(self.rows)


class _NeighborVisibilityCursor:
    buildings = (
        {"building_id": "consented", "verified": True, "city_id": "baden"},
        {"building_id": "revoked", "verified": True, "city_id": "baden"},
        {"building_id": "missing", "verified": True, "city_id": "baden"},
        {"building_id": "other-city", "verified": True, "city_id": "aarau"},
    )

    def __init__(self):
        self.consents = {"consented": True, "revoked": False, "other-city": True}

    def execute(self, query, params=None):
        self.query = " ".join(query.split())
        self.params = params or ()

    def _visible(self):
        rows = list(self.buildings)
        has_consent_join = "JOIN consents" in self.query
        has_consent_predicate = "share_with_neighbors = TRUE" in self.query
        if has_consent_join and has_consent_predicate:
            rows = [
                row for row in rows if self.consents.get(row["building_id"]) is True
            ]
        if "city_id = %s" in self.query:
            rows = [row for row in rows if row["city_id"] == self.params[0]]
        return rows

    def fetchall(self):
        return self._visible()

    def fetchone(self):
        return {"count": len(self._visible())}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _NeighborVisibilityConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _neighbor_visibility_connection(monkeypatch):
    cursor = _NeighborVisibilityCursor()

    @contextmanager
    def connection():
        yield _NeighborVisibilityConnection(cursor)

    monkeypatch.setattr(database, "get_connection", connection)
    return cursor


def test_neighbor_queries_exclude_revoked_and_missing_consent(monkeypatch):
    _neighbor_visibility_connection(monkeypatch)

    assert database.get_neighbor_count_near(47.47, 8.30) == 2
    assert {row["building_id"] for row in database.get_all_buildings()} == {
        "consented",
        "other-city",
    }


def test_neighbor_queries_keep_consented_buildings_and_city_scope(monkeypatch):
    _neighbor_visibility_connection(monkeypatch)

    assert database.get_neighbor_count_near(47.47, 8.30, city_id="baden") == 1
    assert [
        row["building_id"] for row in database.get_all_buildings(city_id="baden")
    ] == ["consented"]


def test_neighbor_visibility_double_requires_predicate(monkeypatch):
    """The double must fail if production joins consents but drops the predicate."""
    cursor = _neighbor_visibility_connection(monkeypatch)

    cursor.execute(
        """
        SELECT b.building_id FROM buildings b
        INNER JOIN consents c ON b.building_id = c.building_id
        WHERE b.verified = TRUE
        """
    )
    assert any(row["building_id"] == "revoked" for row in cursor.fetchall())


def test_get_all_municipality_profile_bfs_numbers_returns_rows(monkeypatch):
    @contextmanager
    def _fake_get_connection():
        yield _FakeConnection([{"bfs_number": 261}, {"bfs_number": 247}])

    monkeypatch.setattr(database, "get_connection", _fake_get_connection)
    result = database.get_all_municipality_profile_bfs_numbers()
    assert sorted(result) == [247, 261]


def test_get_all_municipality_profile_bfs_numbers_returns_empty_on_error(monkeypatch):
    @contextmanager
    def _broken_get_connection():
        raise RuntimeError("db down")
        yield

    monkeypatch.setattr(database, "get_connection", _broken_get_connection)
    assert database.get_all_municipality_profile_bfs_numbers() == []


def test_get_profile_bfs_missing_elcom_tariffs_returns_rows(monkeypatch):
    @contextmanager
    def _fake_get_connection():
        yield _FakeConnection([{"bfs_number": 1001}, {"bfs_number": 1002}])

    monkeypatch.setattr(database, "get_connection", _fake_get_connection)
    assert database.get_profile_bfs_missing_elcom_tariffs(year=2026, limit=2) == [
        1001,
        1002,
    ]


def test_get_profile_bfs_missing_elcom_tariffs_returns_empty_on_error(monkeypatch):
    @contextmanager
    def _broken_get_connection():
        raise RuntimeError("db down")
        yield

    monkeypatch.setattr(database, "get_connection", _broken_get_connection)
    assert database.get_profile_bfs_missing_elcom_tariffs(year=2026, limit=2) == []
