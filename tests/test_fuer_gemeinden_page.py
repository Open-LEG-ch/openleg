# SPDX-License-Identifier: AGPL-3.0-or-later
import os
from unittest.mock import MagicMock, patch


class TestFuerGemeindenPage:
    def test_page_renders(self):
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
                resp = client.get("/fuer-gemeinden", follow_redirects=True)
            finally:
                app.before_request_funcs[None] = hooks
            assert resp.status_code == 200
            html = resp.data.decode("utf-8", errors="ignore")
            assert "OpenLEG für Gemeinden" in html
            assert "Selbst betreiben" in html
            assert "Gehostet" in html
            assert "github.com/Open-LEG-ch/openleg" in html
            assert "github.com/openleg-ch/openleg" not in html

    def test_activation_story_renders_dashboard_responsibilities_and_models(self):
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
                response = client.get("/fuer-gemeinden")
            finally:
                app.before_request_funcs[None] = hooks

        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'src="/static/images/screenshots/dashboard-gemeinde.webp"' in html
        assert 'width="1360"' in html
        assert 'height="900"' in html
        assert 'loading="lazy"' in html
        for party in ("Gemeinde", "OpenLEG", "Verteilnetzbetreiber"):
            assert f'data-responsible-party="{party}"' in html
        for source in (
            "Art. 17d und 17e StromVG",
            "Art. 19e bis 19h StromVV",
            "Art. 8a decies Abs. 6 StromVV",
        ):
            assert source in html
        assert html.count('data-source-marker="legal"') >= 2
        assert 'data-testid="data-provenance"' in html
        dimensions = ("Betrieb", "Datenhaltung", "Aufwand", "Einstieg")
        for model in ("self-hosted", "hosted"):
            assert f'data-operating-model="{model}"' in html
            for dimension in dimensions:
                assert f'data-model-dimension="{model}:{dimension}"' in html

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
        base_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "templates",
            "base.html",
        )
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "templates",
            "fuer_gemeinden.html",
        )
        with open(base_path, encoding="utf-8") as handle:
            base = handle.read()
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
        assert '{% extends "base.html" %}' in content
        assert (
            '{% from "partials/page_meta.html" import page_meta with context %}'
            in content
        )
        assert "page_meta(" in content
        assert "partials/tailwind_brand.html" in base
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
        assert "github.com/Open-LEG-ch/openleg" in content

    def test_site_nav_has_skip_link_and_focus_target(self):
        template_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "templates",
            "partials",
            "site_nav.html",
        )
        base_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "templates",
            "base.html",
        )
        css_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static",
            "css",
            "tailwind.css",
        )
        with open(template_path, encoding="utf-8") as handle:
            content = handle.read()
        with open(base_path, encoding="utf-8") as handle:
            base = handle.read()
        with open(css_path, encoding="utf-8") as handle:
            css = handle.read()

        assert 'class="skip-link"' in content
        assert 'href="#main-content"' in content
        assert 'id="main-content"' not in content
        assert 'id="main-content"' in base
        assert 'tabindex="-1"' in base
        assert ".skip-link:focus" in css
