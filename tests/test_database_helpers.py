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
        if "JOIN consents" in self.query:
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


class _ClusterConsentCursor:
    """Simulate the cluster_info/clusters/buildings/consents join for get_all_clusters."""

    def __init__(self):
        self.query = ""
        self.cluster_info = [
            {
                "cluster_id": 1,
                "autarky_percent": 50.0,
                "num_members": 3,
                "polygon": None,
            }
        ]
        self.clusters = [
            {"cluster_id": 1, "building_id": "a"},
            {"cluster_id": 1, "building_id": "b"},
            {"cluster_id": 1, "building_id": "c"},
        ]
        self.buildings = {
            "a": {"building_id": "a", "lat": 47.1, "lon": 8.1},
            "b": {"building_id": "b", "lat": 47.2, "lon": 8.2},
            "c": {"building_id": "c", "lat": 47.3, "lon": 8.3},
        }
        self.consents = {
            "a": {"share_with_neighbors": True},
            "b": {"share_with_neighbors": True},
            "c": {"share_with_neighbors": False},
        }

    def execute(self, query, _params=None):
        self.query = " ".join(query.split())

    def _consenting_members(self):
        members = []
        for c in self.clusters:
            b = self.buildings.get(c["building_id"])
            consent = self.consents.get(c["building_id"])
            if (
                c["building_id"] is not None
                and b is not None
                and b["lat"] is not None
                and b["lon"] is not None
                and consent is not None
                and consent["share_with_neighbors"] is True
            ):
                members.append(
                    {
                        "building_id": b["building_id"],
                        "lat": b["lat"],
                        "lon": b["lon"],
                    }
                )
        members.sort(key=lambda m: m["building_id"])
        return members

    def fetchall(self):
        if "FROM cluster_info ci" not in self.query:
            return []

        members = self._consenting_members()
        info = self.cluster_info[0]

        # The bug: the old SQL selects the stored ci.num_members, which counts
        # all cluster members, while the json_agg FILTER hides non-consenting
        # members. The fix uses COUNT(...) FILTER with the same conditions.
        if "ci.num_members" in self.query:
            num_members = info["num_members"]
        else:
            num_members = len(members)

        return [
            {
                "cluster_id": info["cluster_id"],
                "autarky_percent": info["autarky_percent"],
                "num_members": num_members,
                "polygon": info["polygon"],
                "members": members,
            }
        ]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _ClusterConsentConnection:
    def __init__(self):
        self._cursor = _ClusterConsentCursor()

    def cursor(self):
        return self._cursor


def test_get_all_clusters_num_members_matches_filtered_members(monkeypatch):
    """The stored num_members counts all members; the returned list is filtered by consent.

    The SQL must compute num_members using the same FILTER as json_agg so the
    count and the list can never disagree.
    """

    @contextmanager
    def connection():
        yield _ClusterConsentConnection()

    monkeypatch.setattr(database, "get_connection", connection)

    rows = database.get_all_clusters()
    assert len(rows) == 1
    cluster = rows[0]
    assert cluster["num_members"] == len(cluster["members"])
    assert cluster["num_members"] == 2
    assert [m["building_id"] for m in cluster["members"]] == ["a", "b"]


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
