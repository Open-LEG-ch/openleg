# SPDX-License-Identifier: AGPL-3.0-or-later
import os
import pytest
from unittest.mock import patch, MagicMock


class TestFuerGemeindenPage:
    def test_page_renders(self):
        with patch.dict(
            os.environ,
            {"DATABASE_URL": "postgresql://x:x@localhost/x", "REDIS_URL": "memory://"},
        ):
            with (
                patch("database.is_db_available", return_value=True),
                patch("database.init_db", return_value=True),
                patch("database._connection_pool", MagicMock()),
            ):
                try:
                    from app import app
                except Exception:
                    pytest.skip("App import requires live DB")

                client = app.test_client()
                hooks = list(app.before_request_funcs.get(None, []))
                app.before_request_funcs[None] = [
                    hook
                    for hook in hooks
                    if not (
                        getattr(hook, "__module__", "").startswith("flask_limiter")
                        or getattr(hook, "__name__", "") == "_check_request_limit"
                    )
                ]
                try:
                    resp = client.get("/fuer-gemeinden")
                finally:
                    app.before_request_funcs[None] = hooks
                assert resp.status_code == 200
                html = resp.data.decode("utf-8", errors="ignore")
                assert "OpenLEG für Gemeinden" in html
                assert "Selbst betreiben" in html
                assert "Gehostet" in html
                assert "github.com/Open-LEG-ch/openleg" in html
                assert "github.com/openleg-ch/openleg" not in html

    def test_homepage_no_full_coverage_claim(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "templates",
            "index.html",
        )
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
        assert "2'131 Schweizer Gemeinden" not in content
        assert "Gemeinde-Profile aus öffentlichen Datenquellen" in content

    def test_homepage_links_to_rangliste(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "templates",
            "index.html",
        )
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
        assert "/rangliste" in content
        assert "Solarnutzung" in content

    def test_fuer_gemeinden_template_links_to_rangliste(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "templates",
            "fuer_gemeinden.html",
        )
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
        assert "Rangliste ansehen" in content
        assert 'href="/rangliste"' in content

    def test_homepage_links_to_open_source_explainer(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "templates",
            "index.html",
        )
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
        assert 'href="/open-source"' in content
        assert "Codebase verstehen" in content

    def test_homepage_address_flow_has_clear_states(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "templates",
            "index.html",
        )
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
        assert 'id="address-status"' in content
        assert 'role="status"' in content
        assert 'aria-describedby="address-status address-privacy"' in content
        assert 'data-address-state="empty"' in content
        assert 'data-address-state="loading"' in content
        assert 'data-address-state="found"' in content
        assert 'data-address-state="not-found"' in content
        assert "Adresse prüfen" in content
        assert "Nachbarn finden" in content
        assert "LEG starten" in content
        assert "nicht verkauft" in content

    def test_homepage_municipality_flow_has_three_steps(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "templates",
            "index.html",
        )
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
        assert "Rang prüfen" in content
        assert "Ziel sehen" in content
        assert "Gemeinde anmelden" in content

    def test_fuer_gemeinden_has_share_metadata_and_open_source_link(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "templates",
            "fuer_gemeinden.html",
        )
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
        assert '<meta name="description"' in content
        assert 'rel="canonical"' in content
        assert 'property="og:title"' in content
        assert 'href="/open-source"' in content

    def test_site_nav_has_mobile_menu_controls(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "templates",
            "partials",
            "site_nav.html",
        )
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
        assert 'id="mobile-menu-toggle"' in content
        assert 'id="mobile-menu"' in content
        assert 'aria-controls="mobile-menu"' in content
        assert 'href="/open-source"' in content
