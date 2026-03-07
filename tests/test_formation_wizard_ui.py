"""Tests for formation wizard UI page."""

from unittest.mock import patch

MOCK_MUNICIPALITY = {
    'id': 1,
    'bfs_number': 261,
    'name': 'Dietikon',
    'kanton': 'ZH',
    'dso_name': 'EKZ',
    'population': 29000,
    'subdomain': 'dietikon',
    'onboarding_status': 'registered',
}

DB = 'municipality.db'


class TestFormationWizardUI:
    """Formation wizard page renders and contains all steps."""

    @patch(f'{DB}.get_municipality', return_value=MOCK_MUNICIPALITY)
    def test_formation_page_renders(self, mock_muni, full_client):
        resp = full_client.get('/gemeinde/formation?subdomain=dietikon')
        assert resp.status_code == 200

    @patch(f'{DB}.get_municipality', return_value=MOCK_MUNICIPALITY)
    def test_formation_page_contains_steps(self, mock_muni, full_client):
        resp = full_client.get('/gemeinde/formation?subdomain=dietikon')
        html = resp.data.decode()
        assert 'Erstellen' in html
        assert 'Einladen' in html
        assert 'Best' in html  # Bestätigen
        assert 'Starten' in html
        assert 'Dokumente' in html
        assert 'Unterschriften' in html
        assert 'VNB' in html

    @patch(f'{DB}.get_municipality', return_value=MOCK_MUNICIPALITY)
    def test_formation_page_has_api_endpoints(self, mock_muni, full_client):
        resp = full_client.get('/gemeinde/formation?subdomain=dietikon')
        html = resp.data.decode()
        assert '/api/formation/create' in html
        assert '/api/formation/invite' in html
        assert '/api/formation/start' in html
        assert '/api/formation/generate-docs' in html
        assert '/api/formation/submit-dso' in html

    @patch(f'{DB}.get_municipality', return_value=None)
    def test_formation_page_no_subdomain_404(self, mock_muni, full_client):
        resp = full_client.get('/gemeinde/formation?subdomain=nonexistent')
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'nicht gefunden' in html

    @patch(f'{DB}.get_municipality', return_value=MOCK_MUNICIPALITY)
    def test_formation_page_shows_municipality_name(self, mock_muni, full_client):
        resp = full_client.get('/gemeinde/formation?subdomain=dietikon')
        html = resp.data.decode()
        assert 'Dietikon' in html
