# SPDX-License-Identifier: AGPL-3.0-or-later
"""PostgreSQL energy values through normal registration and shared simulation."""

import math
from decimal import Decimal
from threading import Event

import pytest

import database as db
import ml_models
from tests.test_i18n import _synthetic_environment
from tests.test_interest_postgres import interest_database

__all__ = ["interest_database"]


@pytest.fixture
def registration_environment(interest_database):
    with _synthetic_environment():
        yield


@pytest.mark.parametrize(
    ("consumption", "solar"),
    [(Decimal("4500.25"), Decimal("8.5")), (None, None)],
)
def test_simulated_energy_preserves_totals_without_inventing_missing_inputs(
    consumption, solar
):
    profile = ml_models.generate_mock_profiles(consumption, solar)
    assert profile["consumption_kw"].sum() * 0.25 == pytest.approx(
        4500.25 if consumption is not None else 0
    )
    assert profile["production_kw"].max() == pytest.approx(
        8.5 if solar is not None else 0
    )
    assert profile.notna().all().all()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("consumption", "solar"),
    [
        (Decimal("4500.25"), Decimal("8.5")),
        (Decimal("4500.25"), None),
        (None, Decimal("8.5")),
        (None, None),
    ],
)
def test_registration_matches_real_numeric_profiles_and_remains_unconfirmed(
    registration_environment, monkeypatch, consumption, solar
):
    import app

    calculated_totals = []
    calculate = ml_models.calculate_community_autarky

    def observe_calculation(*args, **kwargs):
        result = calculate(*args, **kwargs)
        calculated_totals.append(result[1])
        return result

    monkeypatch.setattr(ml_models, "calculate_community_autarky", observe_calculation)
    assert db.save_building(
        "existing-household",
        "existing@example.org",
        {
            "address": "Fiktiver Weg 1",
            "lat": 47.1,
            "lon": 8.1,
            "bfs_number": 999999,
            "annual_consumption_kwh": consumption,
            "potential_pv_kwp": solar,
        },
        {"share_with_neighbors": True},
        city_id="baden",
        verified=True,
    )
    delivered = Event()
    messages = []

    def capture(email, url, building_id, address, **kwargs):
        messages.append((email, url, building_id))
        delivered.set()

    monkeypatch.setattr(app, "send_confirmation_email", capture)
    web = app.create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "native-energy-profile-test",
            "APP_BASE_URL": "http://localhost",
            "RATELIMIT_STORAGE_URI": "memory://",
        },
        load_environment=False,
        check_database=False,
    )
    response = web.test_client().post(
        "/api/register_full",
        json={
            "email": "new@example.org",
            "profile": {
                "building_id": "new-household",
                "address": "Fiktiver Weg 2",
                "lat": 47.1001,
                "lon": 8.1001,
                "bfs_number": 999999,
                "annual_consumption_kwh": 4200,
                "potential_pv_kwp": 0,
            },
        },
    )
    assert response.status_code == 200
    assert response.json["verification_email_sent"] is True
    assert response.json["cluster_info"]["num_members"] == 2
    score = response.json["cluster_info"]["autarky_percent"]
    assert math.isfinite(score) and 0 <= score <= 100
    assert calculated_totals == pytest.approx(
        [8700.25 if consumption is not None else 4200]
    )
    if solar is None:
        assert score == 0
    profile = db.get_building("new-household")
    assert profile["verified"] is False
    assert profile["annual_consumption_kwh"] == 4200
    assert delivered.wait(timeout=5)
    assert messages[0][0] == "new@example.org"
    assert "/confirm/" in messages[0][1]
    assert messages[0][2] == "new-household"
