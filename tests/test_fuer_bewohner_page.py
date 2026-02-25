import os
import pytest
from unittest.mock import patch, MagicMock


class TestFuerBewohnerPage:
    """Tests for /fuer-bewohner page content refresh + embedded registration."""

    def _get_html(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://x:x@localhost/x", "REDIS_URL": "memory://"}):
            with patch("database.is_db_available", return_value=True), \
                 patch("database.init_db", return_value=True), \
                 patch("database._connection_pool", MagicMock()), \
                 patch("database.get_stats", return_value={"total_buildings": 5}):
                try:
                    from app import app
                except Exception:
                    pytest.skip("App import requires live DB")

                client = app.test_client()
                hooks = list(app.before_request_funcs.get(None, []))
                app.before_request_funcs[None] = [
                    hook for hook in hooks
                    if not (
                        getattr(hook, "__module__", "").startswith("flask_limiter")
                        or getattr(hook, "__name__", "") == "_check_request_limit"
                    )
                ]
                try:
                    resp = client.get("/fuer-bewohner")
                finally:
                    app.before_request_funcs[None] = hooks
                assert resp.status_code == 200
                return resp.data.decode("utf-8", errors="ignore")

    def test_page_renders_with_updated_content(self):
        html = self._get_html()
        assert "Für Bewohner" in html
        assert "Eigentümer mit PV" in html
        assert "Mieter" in html

    def test_honest_savings_messaging(self):
        html = self._get_html()
        assert "bis 40%" not in html
        assert "Betriebsdaten" in html or "Hochrechnungen" in html

    def test_social_coordination_framing(self):
        html = self._get_html()
        assert "Koordination" in html or "koordiniert" in html

    def test_registration_form_embedded(self):
        html = self._get_html()
        assert 'id="address-input"' in html
        assert 'id="btn-check"' in html
        assert 'id="email-input"' in html
        assert 'id="btn-register"' in html
        assert 'id="step-success"' in html

    def test_form_calls_correct_api(self):
        html = self._get_html()
        assert "/api/register_anonymous" in html
        assert "/api/register_interest" not in html

    def test_no_redirect_to_homepage(self):
        html = self._get_html()
        assert "/#registrieren" not in html

    def test_no_dashes_in_content(self):
        html = self._get_html()
        assert "\u2014" not in html  # em dash
        assert "\u2013" not in html  # en dash
