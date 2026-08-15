# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dashboard demo route tests."""

import importlib
import os
from unittest.mock import MagicMock, patch

import pytest

from tests.test_app_organic_routes import _disable_rate_limit_hooks


@pytest.fixture
def app_module():
    with (
        patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql://x:x@localhost/x",
                "REDIS_URL": "memory://",
                "CRON_SECRET": "test-cron-secret",
                "APP_BASE_URL": "http://localhost:5003",
            },
        ),
        patch("database.is_db_available", return_value=True),
        patch("database._connection_pool", MagicMock()),
    ):
        import app as imported_app

        imported_app = importlib.reload(imported_app)
        imported_app.web = imported_app.create_app(load_environment=False)
        hooks = _disable_rate_limit_hooks(imported_app.web)
        try:
            yield imported_app
        finally:
            imported_app.web.before_request_funcs[None] = hooks


def test_dashboard_demo_route_renders_fake_data(app_module):
    client = app_module.web.test_client()
    response = client.get("/dashboard/demo")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "LEG-Dashboard" in html
    assert "Mellingerstrasse 12, 5400 Baden" in html
    assert "https://openleg.ch/?ref=DEMO-LEG" in html
    assert "/api/calculate_savings" in html


def test_readiness_counts_neighbors_at_zero_coordinates(monkeypatch):
    import dashboard

    monkeypatch.setattr(
        dashboard.db,
        "get_building_for_dashboard",
        lambda building_id: {
            "building_id": building_id,
            "lat": 0.0,
            "lon": 0.0,
            "verified": True,
            "annual_consumption_kwh": 4200,
            "share_with_utility": True,
            "share_with_neighbors": True,
        },
    )
    monkeypatch.setattr(dashboard.db, "get_referral_code", lambda building_id: None)
    monkeypatch.setattr(
        dashboard.db,
        "get_neighbor_count_near",
        lambda lat, lon, city_id=None: 7 if (lat, lon) == (0.0, 0.0) else 0,
    )

    result = dashboard.readiness("demo")

    assert result["neighbor_count"] == 7


def test_dashboard_uses_brand_system_not_bespoke_css(app_module):
    client = app_module.web.test_client()
    html = client.get("/dashboard/demo").get_data(as_text=True)
    # the old template shipped its own .dashboard-* stylesheet
    assert ".dashboard-wrap{" not in html.replace(" ", "")
    assert "/static/css/openleg.css" in html


def test_dashboard_open_checks_are_actionable(app_module):
    client = app_module.web.test_client()
    html = client.get("/dashboard/demo").get_data(as_text=True)
    # demo data has exactly one open check; it must carry an action link
    assert html.count("check-action") >= 1


def test_dashboard_calculator_has_visible_feedback(app_module):
    client = app_module.web.test_client()
    html = client.get("/dashboard/demo").get_data(as_text=True)
    assert 'id="calc-error"' in html


def test_dashboard_internal_links_resolve(app_module):
    import re as _re

    client = app_module.web.test_client()
    html = client.get("/dashboard/demo").get_data(as_text=True)
    adapter = app_module.web.url_map.bind("localhost")
    hrefs = {
        h.split("#")[0].split("?")[0]
        for h in _re.findall(r'href="(/[^"]*)"', html)
        if not h.startswith("/static/")
    }
    dead = []
    for href in sorted(h for h in hrefs if h):
        try:
            adapter.match(href)
        except Exception:
            dead.append(href)
    assert dead == []


def test_calculate_savings_estimate_returns_solar_and_self_consumption_assumptions():
    from formation_wizard import calculate_savings_estimate

    result = calculate_savings_estimate(
        consumption_kwh=4500, pv_kwp=0, community_size=5
    )
    assert result["assumptions"]["solar_kwh_per_kwp"] == 950
    assert result["assumptions"]["self_consumption_share_pct"] == 30.0


def test_calculate_savings_estimate_pins_canonical_output():
    from formation_wizard import calculate_savings_estimate

    result = calculate_savings_estimate(
        consumption_kwh=4500, pv_kwp=10, community_size=5
    )
    assert result["annual_savings_chf"] == 1140.0
    assert result["monthly_savings_chf"] == 95.0
    assert result["five_year_savings_chf"] == 5700.0


def test_savings_api_includes_assumptions(app_module):
    client = app_module.web.test_client()
    resp = client.post(
        "/api/calculate_savings",
        json={"consumption_kwh": 4500, "has_solar": False, "pv_kwp": 0},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "assumptions" in data
    for key in (
        "grid_buy_price_rp",
        "grid_sell_price_rp",
        "leg_price_rp",
        "community_size",
        "solar_kwh_per_kwp",
        "self_consumption_share_pct",
    ):
        assert key in data["assumptions"]


def test_savings_api_assumptions_track_backend_constants(app_module, monkeypatch):
    monkeypatch.setattr(app_module.formation_wizard, "DEFAULT_GRID_BUY_PRICE_RP", 30.0)
    client = app_module.web.test_client()
    resp = client.post(
        "/api/calculate_savings",
        json={"consumption_kwh": 4500, "has_solar": False, "pv_kwp": 0},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["assumptions"]["grid_buy_price_rp"] == 30.0
    assert data["annual_savings_chf"] == 202.5


@pytest.mark.parametrize(
    ("has_solar", "pv_kwp", "expected_annual_savings"),
    ((False, 0, 135.0), (True, 10, 1140.0)),
)
def test_savings_api_pins_canonical_output(
    app_module, has_solar, pv_kwp, expected_annual_savings
):
    client = app_module.web.test_client()
    resp = client.post(
        "/api/calculate_savings",
        json={
            "consumption_kwh": 4500,
            "has_solar": has_solar,
            "pv_kwp": pv_kwp,
        },
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["annual_savings_chf"] == expected_annual_savings
    assert set(data) == {
        "annual_savings_chf",
        "monthly_savings_chf",
        "five_year_savings_chf",
        "assumptions",
    }


def test_savings_api_matches_uncapped_canonical_function(app_module):
    client = app_module.web.test_client()
    resp = client.post(
        "/api/calculate_savings",
        json={"consumption_kwh": 4500, "has_solar": True, "pv_kwp": 20},
    )

    assert resp.status_code == 200
    endpoint_result = resp.get_json()
    canonical_result = app_module.formation_wizard.calculate_savings_estimate(
        consumption_kwh=4500,
        pv_kwp=20,
        community_size=5,
    )
    assert canonical_result["annual_savings_chf"] == 2160.0
    assert (
        endpoint_result["annual_savings_chf"] == canonical_result["annual_savings_chf"]
    )


def test_savings_api_uses_tenant_solar_yield_override(app_module, monkeypatch):
    tenant = {**app_module.tenant_module.DEFAULT_TENANT, "solar_kwh_per_kwp": 875}
    monkeypatch.setattr(
        app_module.tenant_module,
        "get_tenant_config",
        lambda _territory, db=None: tenant,
    )
    client = app_module.web.test_client()
    resp = client.post(
        "/api/calculate_savings",
        json={"consumption_kwh": 4500, "has_solar": True, "pv_kwp": 10},
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["assumptions"]["solar_kwh_per_kwp"] == 875
    assert data["annual_savings_chf"] == 1050.0


def test_dashboard_renders_every_savings_assumption_from_api(app_module):
    client = app_module.web.test_client()
    html = client.get("/dashboard/demo").get_data(as_text=True)

    for label in (
        "Netzbezugspreis",
        "Einspeisevergütung",
        "LEG-Preis",
        "Haushalte in der LEG",
        "PV-Ertrag pro kWp",
        "Eigenverbrauchsanteil",
    ):
        assert label in html

    for key in (
        "grid_buy_price_rp",
        "grid_sell_price_rp",
        "leg_price_rp",
        "community_size",
        "solar_kwh_per_kwp",
        "self_consumption_share_pct",
    ):
        assert f"data.assumptions.{key}" in html
