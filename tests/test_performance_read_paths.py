# SPDX-License-Identifier: AGPL-3.0-or-later
"""Query-count contracts for stable public read paths (#288)."""

from contextlib import contextmanager
from unittest.mock import MagicMock

import database
from tests.test_app_organic_routes import (  # noqa: F401
    full_app_module as organic_app_module,
)


def test_sitemap_reuses_rendered_xml_within_cache_window(
    organic_app_module,  # noqa: F811
    monkeypatch,
):
    values = {}
    load_ids = MagicMock(return_value=[261, 247])
    monkeypatch.setattr(
        organic_app_module.db, "get_all_municipality_profile_bfs_numbers", load_ids
    )
    monkeypatch.setattr(
        organic_app_module.cache_module, "cache_get", lambda key: values.get(key)
    )
    monkeypatch.setattr(
        organic_app_module.cache_module,
        "cache_set",
        lambda key, value, ttl: values.__setitem__(key, value),
    )
    client = organic_app_module.web.test_client()

    first = client.get("/sitemap.xml")
    second = client.get("/sitemap.xml")

    assert first.status_code == second.status_code == 200
    assert first.data == second.data
    assert load_ids.call_count == 1
    assert values


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def test_clusters_bulk_load_member_coordinates(monkeypatch):
    cursor = _Cursor(
        [
            {
                "cluster_id": 7,
                "autarky_percent": 42,
                "num_members": 2,
                "polygon": None,
                "members": [
                    {"building_id": "a", "lat": 47.1, "lon": 8.1},
                    {"building_id": "b", "lat": 47.2, "lon": 8.2},
                ],
            }
        ]
    )

    @contextmanager
    def connection():
        yield _Connection(cursor)

    monkeypatch.setattr(database, "get_connection", connection)

    rows = database.get_all_clusters()

    query = " ".join(cursor.executed[0][0].split())
    assert len(cursor.executed) == 1
    assert "JOIN buildings" in query
    assert rows[0]["members"][0] == {
        "building_id": "a",
        "lat": 47.1,
        "lon": 8.1,
    }


def test_cluster_route_never_loads_members_one_by_one(
    organic_app_module,  # noqa: F811
    monkeypatch,
):
    monkeypatch.setattr(
        organic_app_module.db,
        "get_all_clusters",
        lambda: [
            {
                "cluster_id": 7,
                "autarky_percent": 42,
                "members": [
                    {"building_id": "a", "lat": 47.1, "lon": 8.1},
                    {"building_id": "b", "lat": 47.2, "lon": 8.2},
                ],
            }
        ],
    )
    get_building = MagicMock(side_effect=AssertionError("N+1 query"))
    monkeypatch.setattr(organic_app_module.db, "get_building", get_building)

    response = organic_app_module.web.test_client().get("/api/get_all_clusters")

    assert response.status_code == 200
    assert len(response.get_json()["clusters"][0]["members"]) == 2
    get_building.assert_not_called()
