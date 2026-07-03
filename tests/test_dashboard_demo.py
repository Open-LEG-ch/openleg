# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dashboard demo route tests."""

import importlib
import os
from unittest.mock import MagicMock, patch

import pytest

from tests.test_app_organic_routes import _disable_rate_limit_hooks


@pytest.fixture
def app_module():
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": "postgresql://x:x@localhost/x",
            "REDIS_URL": "memory://",
            "CRON_SECRET": "test-cron-secret",
            "APP_BASE_URL": "http://localhost:5003",
        },
    ):
        with (
            patch("database.is_db_available", return_value=True),
            patch("database._connection_pool", MagicMock()),
        ):
            import app as imported_app

            imported_app = importlib.reload(imported_app)
            hooks = _disable_rate_limit_hooks(imported_app.app)
            try:
                yield imported_app
            finally:
                imported_app.app.before_request_funcs[None] = hooks


def test_dashboard_demo_route_renders_fake_data(app_module):
    client = app_module.app.test_client()
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
