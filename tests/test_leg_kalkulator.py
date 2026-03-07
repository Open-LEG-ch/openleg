import os
from unittest.mock import MagicMock, patch

import pytest


class TestLegKalkulatorPage:
    """Tests for /leg-kalkulator savings calculator page."""

    def _get_html(self):
        with patch.dict(os.environ, {'DATABASE_URL': 'postgresql://x:x@localhost/x', 'REDIS_URL': 'memory://'}):
            with (
                patch('database.is_db_available', return_value=True),
                patch('database.init_db', return_value=True),
                patch('database._connection_pool', MagicMock()),
                patch('database.get_stats', return_value={'total_buildings': 5}),
            ):
                try:
                    from app import app
                except Exception:
                    pytest.skip('App import requires live DB')

                client = app.test_client()
                hooks = list(app.before_request_funcs.get(None, []))
                app.before_request_funcs[None] = [
                    hook
                    for hook in hooks
                    if not (
                        getattr(hook, '__module__', '').startswith('flask_limiter')
                        or getattr(hook, '__name__', '') == '_check_request_limit'
                    )
                ]
                try:
                    resp = client.get('/leg-kalkulator')
                finally:
                    app.before_request_funcs[None] = hooks
                assert resp.status_code == 200
                return resp.data.decode('utf-8', errors='ignore')

    def test_route_returns_200(self):
        self._get_html()

    def test_has_h1(self):
        html = self._get_html()
        assert '<h1' in html
        assert 'kalkulator' in html.lower() or 'rechner' in html.lower()

    def test_has_canonical(self):
        html = self._get_html()
        assert 'rel="canonical"' in html
        assert '/leg-kalkulator' in html

    def test_has_meta_description(self):
        html = self._get_html()
        assert 'name="description"' in html

    def test_has_schema_markup(self):
        html = self._get_html()
        assert 'WebApplication' in html or 'FAQPage' in html

    def test_has_og_tags(self):
        html = self._get_html()
        assert 'og:title' in html
        assert 'og:description' in html
        assert 'og:url' in html

    def test_in_sitemap(self):
        with patch.dict(os.environ, {'DATABASE_URL': 'postgresql://x:x@localhost/x', 'REDIS_URL': 'memory://'}):
            with (
                patch('database.is_db_available', return_value=True),
                patch('database.init_db', return_value=True),
                patch('database._connection_pool', MagicMock()),
                patch('database.get_stats', return_value={'total_buildings': 5}),
                patch('database.get_all_municipality_profiles', return_value=[]),
            ):
                try:
                    from app import app
                except Exception:
                    pytest.skip('App import requires live DB')

                client = app.test_client()
                hooks = list(app.before_request_funcs.get(None, []))
                app.before_request_funcs[None] = [
                    hook
                    for hook in hooks
                    if not (
                        getattr(hook, '__module__', '').startswith('flask_limiter')
                        or getattr(hook, '__name__', '') == '_check_request_limit'
                    )
                ]
                try:
                    resp = client.get('/sitemap.xml')
                finally:
                    app.before_request_funcs[None] = hooks
                assert resp.status_code == 200
                assert '/leg-kalkulator' in resp.data.decode('utf-8')

    def test_has_calculator_form(self):
        html = self._get_html()
        assert '<form' in html.lower() or 'id=' in html.lower()
        assert 'plz' in html.lower() or 'gemeinde' in html.lower()
        assert 'kwh' in html.lower() or 'verbrauch' in html.lower()

    def test_api_integration(self):
        html = self._get_html()
        assert '/api/v1/municipalities' in html or '/api/v1/leg/financial-model' in html

    def test_no_dashes_in_content(self):
        html = self._get_html()
        assert '\u2014' not in html
        assert '\u2013' not in html
