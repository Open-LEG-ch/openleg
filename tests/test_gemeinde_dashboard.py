# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract tests for the municipality dashboard (brand system, honest data)."""

import os
import re
from unittest.mock import MagicMock, patch


def _client():
    with (
        patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql://x:x@localhost/x",
                "REDIS_URL": "memory://",
                "APP_BASE_URL": "http://localhost:5003",
            },
        ),
        patch("database.is_db_available", return_value=True),
        patch("database.init_db", return_value=True),
        patch("database._connection_pool", MagicMock()),
    ):
        from app import create_app

        app = create_app(load_environment=False)
        return app.test_client()


class TestGemeindeDashboardDemo:
    def test_demo_route_renders(self):
        client = _client()
        resp = client.get("/gemeinde/dashboard/demo")
        assert resp.status_code == 200
        assert "Baden" in resp.get_data(as_text=True)

    def test_no_tailwind_cdn(self):
        client = _client()
        html = client.get("/gemeinde/dashboard/demo").get_data(as_text=True)
        assert "cdn.tailwindcss.com" not in html

    def test_uses_built_stylesheet_and_shared_nav(self):
        client = _client()
        html = client.get("/gemeinde/dashboard/demo").get_data(as_text=True)
        assert "/static/css/openleg.css" in html
        # base.html brings the shared footer; the page must not be standalone
        assert "site-footer" in html or "footer" in html.lower()

    def test_no_raw_status_enum(self):
        client = _client()
        html = client.get("/gemeinde/dashboard/demo").get_data(as_text=True)
        assert "onboarding_status" not in html
        assert "Status: pending" not in html
        assert "Status: active" not in html

    def test_no_invented_dso_fallback(self):
        client = _client()
        html = client.get("/gemeinde/dashboard/demo").get_data(as_text=True)
        assert "EKZ" not in html

    def test_invite_link_uses_municipality_subdomain(self):
        client = _client()
        html = client.get("/gemeinde/dashboard/demo").get_data(as_text=True)
        assert "baden.openleg.ch" in html

    def test_open_checklist_items_are_actionable(self):
        client = _client()
        html = client.get("/gemeinde/dashboard/demo").get_data(as_text=True)
        # every open next step links somewhere concrete
        assert 'href="/leg-gruenden"' in html
        assert "hallo@openleg.ch" in html or 'href="/fuer-gemeinden"' in html

    def test_demo_shows_demo_stats_not_hardcoded_zeros(self):
        client = _client()
        html = client.get("/gemeinde/dashboard/demo").get_data(as_text=True)
        assert "42" in html
        assert ">0<" not in html.replace(" ", "")

    def test_solar_score_links_to_methodology(self):
        client = _client()
        html = client.get("/gemeinde/dashboard/demo").get_data(as_text=True)
        tile = re.search(r"Solarnutzung.*?</div>", html, re.DOTALL)
        assert tile
        assert 'href="/rangliste/methodik"' in tile.group(0)
        assert "Formel und Datengrenzen ansehen" in tile.group(0)

    def test_energy_score_shows_weighting_and_links_to_methodology(self):
        client = _client()
        html = client.get("/gemeinde/dashboard/demo").get_data(as_text=True)
        tile = re.search(r"Energiewende-Score.*?</div>", html, re.DOTALL)
        assert tile
        assert 'href="/rangliste/methodik"' in tile.group(0)
        assert (
            "Gewichtung: Solar 30 Prozent, Elektroautos 20 Prozent, erneuerbare "
            "Heizungen 25 Prozent, erneuerbare Produktion 25 Prozent." in tile.group(0)
        )


class TestGemeindeDashboardEmptyState:
    def test_anonymous_dashboard_shows_access_request(self):
        client = _client()
        with patch("database.get_municipality", return_value=None):
            resp = client.get("/gemeinde/dashboard")
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert 'action="/gemeinde/access/request"' in html
        assert "cdn.tailwindcss.com" not in html
