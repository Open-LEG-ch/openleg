"""Tests for Gemeinde dashboard with live data + formation trigger."""

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

MOCK_STATS = {
    'total_buildings': 12,
    'registrations_today': 2,
    'total_referrals': 5,
}

MOCK_DASHBOARD_STATS = {
    'community_count': 2,
    'confirmed_members': 7,
    'meter_uploads': 4,
    'formation_ready_count': 5,
}

# Patch target: municipality.db is the reference used at call time
DB = 'municipality.db'


class TestDashboardLiveData:
    """Dashboard shows live community/member/meter stats."""

    @patch(f'{DB}.get_dashboard_stats', return_value=MOCK_DASHBOARD_STATS)
    @patch(f'{DB}.get_stats', return_value=MOCK_STATS)
    @patch(f'{DB}.get_municipality', return_value=MOCK_MUNICIPALITY)
    def test_dashboard_shows_community_count(self, mock_muni, mock_stats, mock_dash, full_client):
        resp = full_client.get('/gemeinde/dashboard?subdomain=dietikon')
        assert resp.status_code == 200
        html = resp.data.decode()
        assert '2' in html  # community_count

    @patch(f'{DB}.get_dashboard_stats', return_value=MOCK_DASHBOARD_STATS)
    @patch(f'{DB}.get_stats', return_value=MOCK_STATS)
    @patch(f'{DB}.get_municipality', return_value=MOCK_MUNICIPALITY)
    def test_dashboard_shows_meter_uploads(self, mock_muni, mock_stats, mock_dash, full_client):
        resp = full_client.get('/gemeinde/dashboard?subdomain=dietikon')
        html = resp.data.decode()
        assert '4' in html  # meter_uploads

    @patch(f'{DB}.get_dashboard_stats', return_value=MOCK_DASHBOARD_STATS)
    @patch(f'{DB}.get_stats', return_value=MOCK_STATS)
    @patch(f'{DB}.get_municipality', return_value=MOCK_MUNICIPALITY)
    def test_dashboard_shows_formation_readiness(self, mock_muni, mock_stats, mock_dash, full_client):
        resp = full_client.get('/gemeinde/dashboard?subdomain=dietikon')
        html = resp.data.decode()
        assert 'LEG-Gr' in html  # "LEG-Gründung" readiness indicator

    @patch(
        f'{DB}.get_dashboard_stats',
        return_value={
            'community_count': 0,
            'confirmed_members': 1,
            'meter_uploads': 0,
            'formation_ready_count': 1,
        },
    )
    @patch(f'{DB}.get_stats', return_value=MOCK_STATS)
    @patch(f'{DB}.get_municipality', return_value=MOCK_MUNICIPALITY)
    def test_dashboard_hides_formation_button_under_3(self, mock_muni, mock_stats, mock_dash, full_client):
        resp = full_client.get('/gemeinde/dashboard?subdomain=dietikon')
        html = resp.data.decode()
        assert '/gemeinde/formation' not in html

    @patch(
        f'{DB}.get_dashboard_stats',
        return_value={
            'community_count': 0,
            'confirmed_members': 5,
            'meter_uploads': 2,
            'formation_ready_count': 5,
        },
    )
    @patch(f'{DB}.get_stats', return_value=MOCK_STATS)
    @patch(f'{DB}.get_municipality', return_value=MOCK_MUNICIPALITY)
    def test_dashboard_shows_formation_button_at_3(self, mock_muni, mock_stats, mock_dash, full_client):
        resp = full_client.get('/gemeinde/dashboard?subdomain=dietikon')
        html = resp.data.decode()
        assert '/gemeinde/formation' in html

    @patch(f'{DB}.get_dashboard_stats', return_value=MOCK_DASHBOARD_STATS)
    @patch(f'{DB}.get_stats', return_value=MOCK_STATS)
    @patch(f'{DB}.get_municipality', return_value=MOCK_MUNICIPALITY)
    def test_dashboard_invite_link_uses_subdomain(self, mock_muni, mock_stats, mock_dash, full_client):
        resp = full_client.get('/gemeinde/dashboard?subdomain=dietikon')
        html = resp.data.decode()
        assert 'dietikon' in html  # subdomain in invite link

    @patch(f'{DB}.get_municipality', return_value=None)
    def test_dashboard_not_found(self, mock_muni, full_client):
        resp = full_client.get('/gemeinde/dashboard?subdomain=nonexistent')
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'nicht gefunden' in html
