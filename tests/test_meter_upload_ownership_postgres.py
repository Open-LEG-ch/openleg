# SPDX-License-Identifier: AGPL-3.0-or-later
"""Meter uploads exercise household authorization with real persisted readings."""

import pytest

from tests import security_review_fixtures
from tests import test_interest_postgres as interest_postgres_tests
from tests.test_dashboard_access_routes import _set_session

app_module = security_review_fixtures.app_module
interest_database = interest_postgres_tests.interest_database

pytestmark = pytest.mark.integration
CSV = "Zeitstempel;Verbrauch (kWh);Produktion (kWh);Einspeisung (kWh)\n01.01.2026 00:15;1.5;0;0\n"


@pytest.fixture
def households(interest_database):
    db = interest_database
    for bid in ("building-session", "victim"):
        assert db.save_building(
            bid,
            f"{bid}@example.ch",
            {"address": "Testweg 1", "lat": 47.2, "lon": 8.2},
            {},
        )
        assert db.save_data_consent(bid, tier=1, share_municipality=False)
    return db


@pytest.mark.parametrize(
    "identity,csrf,status",
    [(False, "csrf-secret", 401), (True, "", 400), (True, "wrong", 400)],
)
def test_unauthorized_upload_does_not_change_readings_or_consent(
    app_module, households, identity, csrf, status
):
    client = app_module.web.test_client()
    if identity:
        _set_session(client)
    response = client.post(
        "/api/meter-data/upload",
        json={
            "building_id": "victim",
            "csv_content": CSV,
            "tier": 3,
            "consent_confirmed": True,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == status
    for bid in ("building-session", "victim"):
        assert households.get_meter_readings(bid) == []
        assert households.get_data_consent(bid)["tier"] == 1


def test_upload_uses_session_household_without_changing_consent_implicitly(
    app_module, households
):
    client = app_module.web.test_client()
    _set_session(client)
    response = client.post(
        "/api/meter-data/upload",
        json={"building_id": "victim", "csv_content": CSV, "tier": 3},
        headers={"X-CSRF-Token": "csrf-secret"},
    )
    assert response.status_code == 200
    assert response.json["success"] is True
    assert len(households.get_meter_readings("building-session")) == 1
    assert households.get_meter_readings("victim") == []
    for bid in ("building-session", "victim"):
        consent = households.get_data_consent(bid)
        assert consent["tier"] == 1
        assert consent["share_anonymized_research"] is False
        assert consent["share_aggregated_providers"] is False


def test_owner_can_explicitly_confirm_the_selected_data_consent(app_module, households):
    client = app_module.web.test_client()
    _set_session(client)
    response = client.post(
        "/api/meter-data/upload",
        json={"csv_content": CSV, "tier": 3, "consent_confirmed": True},
        headers={"X-CSRF-Token": "csrf-secret"},
    )
    assert response.status_code == 200
    consent = households.get_data_consent("building-session")
    assert consent["tier"] == 3
    assert consent["share_aggregated_providers"] is True
    assert households.get_data_consent("victim")["tier"] == 1
