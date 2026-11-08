# SPDX-License-Identifier: AGPL-3.0-or-later
"""Formation counts and distances use current consent in real PostgreSQL."""

import pytest

from tests import security_review_fixtures, test_interest_postgres
from tests.test_dashboard_access_routes import _set_session

app_module = security_review_fixtures.app_module
interest_database = test_interest_postgres.interest_database
pytestmark = pytest.mark.integration


def test_formation_reads_neighbours_and_honours_revoked_consent(
    app_module, interest_database
):
    db = interest_database
    for index, bid in enumerate(("owner", "sharing-one", "sharing-two", "revoked")):
        assert db.save_building(
            bid,
            f"{bid}@example.ch",
            {"address": "Privateweg 1", "lat": 47.2 + index * 0.0001, "lon": 8.2},
            {"share_with_neighbors": bid != "revoked"},
            verified=True,
        )
    client = app_module.web.test_client()
    _set_session(client, "owner")
    response = client.post("/api/formation/optimize", json={"building_id": "revoked"})
    assert response.status_code == 200
    assert len(response.json["clusters"]) == 1
    cluster = response.json["clusters"][0]
    assert cluster["potential_members"] == 3
    assert len(cluster["nearby_buildings"]) == 2
    assert all(
        set(row) == {"distance"} and 0 < row["distance"] < 150
        for row in cluster["nearby_buildings"]
    )
    assert db.update_dashboard_profile(
        "sharing-one",
        annual_consumption_kwh=4500,
        potential_pv_kwp=0,
        share_with_utility=False,
        share_with_neighbors=False,
    )
    assert client.post("/api/formation/optimize").json == {"clusters": []}
