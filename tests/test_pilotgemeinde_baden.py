import os
from unittest.mock import MagicMock, patch

import pytest

MOCK_BADEN_TARIFFS = [
    {
        'category': 'H4',
        'operator_name': 'Regionalwerke AG Baden',
        'total_rp_kwh': 27.45,
        'grid_rp_kwh': 9.82,
        'energy_rp_kwh': 12.63,
        'levy_rp_kwh': 5.0,
        'year': 2026,
        'bfs_number': 4021,
    },
    {
        'category': 'C2',
        'operator_name': 'Regionalwerke AG Baden',
        'total_rp_kwh': 22.10,
        'grid_rp_kwh': 7.50,
        'energy_rp_kwh': 10.60,
        'levy_rp_kwh': 4.0,
        'year': 2026,
        'bfs_number': 4021,
    },
]

MOCK_BADEN_PROFILE = {
    'bfs_number': 4021,
    'name': 'Baden',
    'kanton': 'AG',
    'population': 19400,
    'energy_transition_score': 62,
}

MOCK_BADEN_SOLAR = {
    'bfs_number': 4021,
    'total_roof_area_m2': 450000,
    'suitable_roof_area_m2': 280000,
    'potential_kwh_year': 38000000,
    'potential_kwp': 35000,
    'utilization_pct': 18.5,
}


def _get_app_module():
    with patch.dict(os.environ, {'DATABASE_URL': 'postgresql://x:x@localhost/x', 'REDIS_URL': 'memory://'}):
        with (
            patch('database.is_db_available', return_value=True),
            patch('database.init_db', return_value=True),
            patch('database._connection_pool', MagicMock()),
            patch('database.get_stats', return_value={'total_buildings': 5}),
        ):
            try:
                import app as app_module
            except Exception:
                pytest.skip('App import requires live DB')
            return app_module


def _get_html(app_module):
    # Patch on app_module.db (the actual reference the route uses) to handle
    # cases where conftest full_app fixture replaced sys.modules['database']
    db_ref = app_module.db
    with (
        patch.object(db_ref, 'get_elcom_tariffs', return_value=MOCK_BADEN_TARIFFS),
        patch.object(db_ref, 'get_municipality_profile', return_value=MOCK_BADEN_PROFILE),
        patch.object(db_ref, 'get_sonnendach_municipal', return_value=MOCK_BADEN_SOLAR),
    ):
        flask_app = app_module.app
        client = flask_app.test_client()
        hooks = list(flask_app.before_request_funcs.get(None, []))
        flask_app.before_request_funcs[None] = [
            hook
            for hook in hooks
            if not (
                getattr(hook, '__module__', '').startswith('flask_limiter')
                or getattr(hook, '__name__', '') == '_check_request_limit'
            )
        ]
        try:
            resp = client.get('/pilotgemeinde/baden')
        finally:
            flask_app.before_request_funcs[None] = hooks
        assert resp.status_code == 200
        return resp.data.decode('utf-8', errors='ignore'), flask_app


class TestPilotgemeindeBaden:
    """Tests for /pilotgemeinde/baden case study page."""

    @pytest.fixture(autouse=True, scope='class')
    def setup_app(self, request):
        app_module = _get_app_module()
        html, flask_app = _get_html(app_module)
        request.cls.app = flask_app
        request.cls.html = html

    def test_route_returns_200(self):
        assert self.html  # already asserted 200 in _get_html

    def test_has_h1_with_baden(self):
        assert '<h1' in self.html
        assert 'Baden' in self.html

    def test_has_canonical(self):
        assert 'rel="canonical"' in self.html
        assert '/pilotgemeinde/baden' in self.html

    def test_has_meta_description(self):
        assert 'name="description"' in self.html

    def test_has_schema_markup(self):
        assert 'Article' in self.html or 'Place' in self.html
        assert 'schema.org' in self.html

    def test_has_og_tags(self):
        assert 'og:title' in self.html
        assert 'og:description' in self.html
        assert 'og:url' in self.html

    def test_shows_real_tariff_data(self):
        assert 'Regionalwerke' in self.html
        assert 'Rp/kWh' in self.html

    def test_shows_savings_chf(self):
        assert 'CHF' in self.html
        # value_gap for grid_rp_kwh=9.82, 40% reduction = 3.928 Rp/kWh * 4500 kWh = CHF 176.76
        assert '176' in self.html

    def test_shows_solar_potential(self):
        assert 'kWp' in self.html or 'Solarpotenzial' in self.html

    def test_shows_vnb_name(self):
        assert 'Regionalwerke AG Baden' in self.html

    def test_shows_timeline(self):
        assert 'Anmeldung' in self.html
        assert 'Smart Meter' in self.html

    def test_has_registration_cta(self):
        assert '/fuer-bewohner' in self.html

    def test_no_developer_content(self):
        # Extract main content (between <main> and </main>) to avoid shared footer/nav
        import re

        main_match = re.search(r'<main[^>]*>(.*?)</main>', self.html, re.DOTALL | re.IGNORECASE)
        main_content = main_match.group(1).lower() if main_match else self.html.lower()
        assert 'docker' not in main_content
        assert 'agpl' not in main_content
        assert 'cors' not in main_content

    def test_no_dashes_in_content(self):
        assert '\u2014' not in self.html
        assert '\u2013' not in self.html

    def test_in_sitemap(self):
        with (
            patch('database.get_all_municipality_profiles', return_value=[]),
        ):
            client = self.app.test_client()
            hooks = list(self.app.before_request_funcs.get(None, []))
            self.app.before_request_funcs[None] = [
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
                self.app.before_request_funcs[None] = hooks
            assert resp.status_code == 200
            assert '/pilotgemeinde/baden' in resp.data.decode('utf-8')

    def test_shows_unhappy_path(self):
        assert 'Austritt' in self.html or 'austreten' in self.html.lower()
        html_lower = self.html.lower()
        assert 'elcom' in html_lower
