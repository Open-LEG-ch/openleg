# SPDX-License-Identifier: AGPL-3.0-or-later
"""Household ownership and disclosure rules at the resident HTTP boundary."""

import pytest

from tests import security_review_fixtures
from tests.test_dashboard_access_routes import _set_session

app_module = security_review_fixtures.app_module


@pytest.mark.parametrize(
    "method,path",
    [("post", "/api/formation/optimize"), ("get", "/api/referral/stats/victim")],
)
def test_anonymous_identifiers_do_not_grant_household_access(app_module, method, path):
    response = getattr(app_module.web.test_client(), method)(
        path, json={"building_id": "victim"}
    )
    assert response.status_code == 401


def test_formation_uses_the_session_and_only_returns_neighbour_distances(
    app_module, monkeypatch
):
    def neighbours(building_id, radius):
        if building_id != "building-session":
            pytest.fail("the request selected another household")
        return [
            {
                "building_id": f"private-{index}",
                "email": "private@example.ch",
                "address": "Privateweg 1",
                "lat": 47.1,
                "lon": 8.1,
                "distance": index + 10,
            }
            for index in range(3)
        ]

    monkeypatch.setattr(app_module.db, "fetch_nearby_consenting_neighbours", neighbours)
    client = app_module.web.test_client()
    _set_session(client)
    response = client.post("/api/formation/optimize", json={"building_id": "victim"})
    assert response.status_code == 200
    assert response.json == {
        "clusters": [
            {
                "potential_members": 4,
                "nearby_buildings": [
                    {"distance": 10},
                    {"distance": 11},
                    {"distance": 12},
                ],
                "radius_meters": 150,
                "ready_to_form": True,
            }
        ]
    }
    assert response.headers["Cache-Control"] == "no-store"


def test_referral_statistics_use_the_session_household(app_module, monkeypatch):
    monkeypatch.setattr(
        app_module.db,
        "get_referral_stats",
        lambda bid: {"total_referrals": 7 if bid == "building-session" else 999},
    )
    monkeypatch.setattr(
        app_module.db,
        "get_referral_code",
        lambda bid: "own-code" if bid == "building-session" else "victim-code",
    )
    client = app_module.web.test_client()
    _set_session(client)
    response = client.get("/api/referral/stats/victim")
    assert response.status_code == 200
    assert response.json["referral_code"] == "own-code"
    assert response.json["total_referrals"] == 7


def test_public_leaderboard_does_not_publish_household_identifiers(
    app_module, monkeypatch
):
    monkeypatch.setattr(
        app_module.db,
        "get_referral_leaderboard",
        lambda **kwargs: [
            {"building_id": "victim", "street": "Privateweg 1", "referral_count": 3}
        ],
    )
    response = app_module.web.test_client().get("/api/referral/leaderboard")
    assert response.json == {"leaderboard": [{"rank": 1, "referral_count": 3}]}
