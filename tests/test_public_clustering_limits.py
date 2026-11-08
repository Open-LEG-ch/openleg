# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bound anonymous clustering before its input reaches dataframe allocation."""

import pytest

from tests import security_review_fixtures
from tests.security_review_fixtures import make_web

app_module = security_review_fixtures.app_module


@pytest.mark.parametrize(
    "buildings,status",
    [
        ([{"lat": 47.2, "lon": 8.2}] * 301, 413),
        ({"lat": [47.2, 47.3], "lon": [8.2, 8.3]}, 400),
        ([None, None], 400),
        ([{"lat": True, "lon": 8.2}] * 2, 400),
        ([{"lat": 91, "lon": 8.2}] * 2, 400),
        ([{"lat": 10**400, "lon": 8.2}] * 2, 400),
        ([{"lat": -(10**400), "lon": 8.2}] * 2, 400),
        ([{"lat": 47.2, "lon": float("inf")}] * 2, 400),
    ],
)
def test_clustering_rejects_oversized_or_malformed_batches(
    app_module, buildings, status
):
    response = app_module.web.test_client().post(
        "/api/v1/leg/cluster", json={"buildings": buildings}
    )
    assert response.status_code == status


def test_clustering_posts_have_the_calculator_rate_limit():
    web = make_web()
    client = web.test_client()
    responses = [
        client.post(
            "/api/v1/leg/cluster",
            json={"buildings": []},
            environ_overrides={"REMOTE_ADDR": "198.51.100.76"},
        )
        for _ in range(31)
    ]
    assert [r.status_code for r in responses[:30]] == [400] * 30
    assert responses[-1].status_code == 429


def test_clustering_accepts_the_maximum_valid_batch(app_module):
    buildings = [
        {"lat": 46 + (index // 30) * 0.02, "lon": 7 + (index % 30) * 0.02}
        for index in range(300)
    ]
    response = app_module.web.test_client().post(
        "/api/v1/leg/cluster", json={"buildings": buildings}
    )
    assert response.status_code == 200
    assert response.json == {"clusters": [], "count": 0}


@pytest.mark.parametrize("body", [[], [1], "invalid", 1])
def test_clustering_rejects_non_object_json(app_module, body):
    response = app_module.web.test_client().post("/api/v1/leg/cluster", json=body)
    assert response.status_code == 400
