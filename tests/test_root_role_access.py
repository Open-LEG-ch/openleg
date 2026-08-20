# SPDX-License-Identifier: AGPL-3.0-or-later
"""HTTP contracts for the dashboard product entry point."""

import re

import pytest

from ranking import Ranking
from tests.test_app_organic_routes import _disable_rate_limit_hooks


@pytest.fixture
def app_module(monkeypatch):
    import app as imported_app

    web = imported_app.create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "root-role-access-test-key",
            "APP_BASE_URL": "http://localhost:5003",
            "RATELIMIT_STORAGE_URI": "memory://",
        },
        load_environment=False,
        check_database=False,
    )
    hooks = _disable_rate_limit_hooks(web)
    monkeypatch.setattr(
        imported_app.db,
        "get_stats",
        lambda city_id=None: {"total_buildings": 0},
    )
    monkeypatch.setattr(imported_app.Ranking, "load", lambda: Ranking([]))
    try:
        imported_app.web = web
        yield imported_app
    finally:
        web.before_request_funcs[None] = hooks


def _hrefs(html):
    return re.findall(r'href="([^"]+)"', html)


def test_anonymous_root_offers_exactly_owner_and_municipality_access(app_module):
    response = app_module.web.test_client().get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Eigentümer" in html
    assert "Bewohner-Dashboard" not in html
    assert "site-nav" not in html
    assert "site-footer" not in html
    assert 'href="/fuer-bewohner"' not in html
    role_hrefs = [
        href for href in _hrefs(html) if href in {"/dashboard", "/gemeinde/dashboard"}
    ]
    assert role_hrefs.count("/dashboard") == 1
    assert role_hrefs.count("/gemeinde/dashboard") == 1
    assert len(role_hrefs) == 2


def test_owner_session_redirects_root_to_owner_dashboard(app_module):
    client = app_module.web.test_client()
    with client.session_transaction() as state:
        state["dashboard_building_id"] = "building-session"

    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


def test_municipality_session_redirects_root_to_municipality_dashboard(app_module):
    client = app_module.web.test_client()
    with client.session_transaction() as state:
        state["municipality_id"] = 7

    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/gemeinde/dashboard")
