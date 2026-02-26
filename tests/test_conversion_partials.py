"""Verify conversion trigger partials render on all public pages."""
import os
import re
import pytest
from unittest.mock import patch, MagicMock


ROUTES = ["/fuer-gemeinden", "/fuer-bewohner", "/how-it-works", "/pricing"]
ROUTES_WITH_COMPETITOR = ["/fuer-gemeinden", "/how-it-works", "/pricing"]


def _get_html(path):
    """GET a page from the real Flask app, return decoded HTML."""
    with patch.dict(os.environ, {"DATABASE_URL": "postgresql://x:x@localhost/x", "REDIS_URL": "memory://"}):
        with patch("database.is_db_available", return_value=True), \
             patch("database.init_db", return_value=True), \
             patch("database._connection_pool", MagicMock()):
            try:
                from app import app
            except Exception:
                pytest.skip("App import requires live DB")

            client = app.test_client()
            hooks = list(app.before_request_funcs.get(None, []))
            app.before_request_funcs[None] = [
                h for h in hooks
                if not (
                    getattr(h, "__module__", "").startswith("flask_limiter")
                    or getattr(h, "__name__", "") == "_check_request_limit"
                )
            ]
            try:
                resp = client.get(path)
            finally:
                app.before_request_funcs[None] = hooks

            assert resp.status_code == 200, f"{path} returned {resp.status_code}"
            return resp.data.decode("utf-8", errors="ignore")


class TestTrustBar:
    @pytest.mark.parametrize("route", ROUTES)
    def test_trust_bar_present(self, route):
        html = _get_html(route)
        assert "Datenquellen" in html, f"trust_bar missing on {route}"
        assert "AGPL-3.0" in html, f"AGPL-3.0 missing on {route}"


class TestCtaGemeinde:
    @pytest.mark.parametrize("route", ROUTES)
    def test_cta_gemeinde_present(self, route):
        html = _get_html(route)
        assert "cta-gemeinde" in html, f"cta-gemeinde missing on {route}"
        assert "/gemeinde/onboarding" in html, f"onboarding link missing on {route}"


class TestCompetitorComparison:
    @pytest.mark.parametrize("route", ROUTES_WITH_COMPETITOR)
    def test_competitor_comparison_present(self, route):
        html = _get_html(route)
        assert "LEGhub" in html, f"LEGhub missing on {route}"
        assert "zevvy" in html, f"zevvy missing on {route}"


class TestSavingsWidget:
    @pytest.mark.parametrize("route", ROUTES)
    def test_savings_widget_present(self, route):
        html = _get_html(route)
        assert "sw-input" in html, f"sw-input missing on {route}"
        assert "/api/v1/municipalities" in html, f"municipalities API missing on {route}"


class TestNoDashes:
    @pytest.mark.parametrize("route", ROUTES)
    def test_no_em_en_dashes(self, route):
        html = _get_html(route)
        # Strip <script> blocks; JS fallback strings may use dashes
        text = re.sub(r"<script[\s\S]*?</script>", "", html)
        assert "\u2014" not in text, f"em dash found on {route}"
        assert "\u2013" not in text, f"en dash found on {route}"
