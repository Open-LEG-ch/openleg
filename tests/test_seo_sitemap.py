"""Tests for SEO: dynamic sitemap with municipality URLs and OG meta tags."""

from unittest.mock import patch

from tests.conftest import MOCK_MUNICIPALITY_PROFILE, MOCK_PROFILES_LIST


class TestSitemap:
    """Test dynamic sitemap generation."""

    @patch('database.get_all_municipality_profiles')
    def test_sitemap_includes_municipality_urls(self, mock_profiles, full_client):
        mock_profiles.return_value = MOCK_PROFILES_LIST
        resp = full_client.get('/sitemap.xml')
        assert resp.status_code == 200
        xml = resp.data.decode()
        assert '/gemeinde/profil/261' in xml
        assert '/gemeinde/profil/247' in xml

    @patch('database.get_all_municipality_profiles', return_value=[])
    def test_sitemap_valid_xml(self, mock_profiles, full_client):
        resp = full_client.get('/sitemap.xml')
        assert resp.status_code == 200
        assert resp.content_type.startswith('application/xml')
        xml = resp.data.decode()
        assert '<urlset' in xml

    @patch('municipality.db')
    def test_profil_handles_missing_data(self, mock_db, full_client):
        mock_db.get_municipality_profile.return_value = {
            'bfs_number': 999,
            'name': 'Testgemeinde',
            'kanton': 'ZH',
            'population': None,
            'solar_potential_pct': None,
            'energy_transition_score': 0,
            'data_sources': {},
        }
        mock_db.get_elcom_tariffs.return_value = []
        mock_db.get_sonnendach_municipal.return_value = None
        resp = full_client.get('/gemeinde/profil/999')
        assert resp.status_code == 200

    @patch('municipality.db')
    def test_profil_has_og_meta_tags(self, mock_db, full_client):
        mock_db.get_municipality_profile.return_value = MOCK_MUNICIPALITY_PROFILE
        mock_db.get_elcom_tariffs.return_value = []
        mock_db.get_sonnendach_municipal.return_value = None
        resp = full_client.get('/gemeinde/profil/261')
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'og:title' in html
        assert 'og:description' in html

    @patch('database.get_all_municipality_profiles')
    def test_verzeichnis_filters_by_kanton(self, mock_profiles, full_client):
        mock_profiles.return_value = MOCK_PROFILES_LIST
        resp = full_client.get('/gemeinde/verzeichnis?kanton=ZH')
        assert resp.status_code == 200
