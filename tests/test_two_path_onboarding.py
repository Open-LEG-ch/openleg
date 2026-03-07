"""Tests for two-path gemeinde onboarding (PLAN.md slices A-F)."""

from unittest.mock import patch


class TestSliceA_OnboardingDecisionPage:
    """Onboarding page shows two-path choice: self-host vs managed."""

    def test_onboarding_returns_200(self, full_client):
        with patch('database.get_stats', return_value={'total_buildings': 0}):
            resp = full_client.get('/gemeinde/onboarding')
            assert resp.status_code == 200

    def test_onboarding_has_self_host_card(self, full_client):
        with patch('database.get_stats', return_value={'total_buildings': 0}):
            resp = full_client.get('/gemeinde/onboarding')
            html = resp.data.decode()
            assert 'github.com' in html.lower() or 'self-host' in html.lower() or 'Selbst betreiben' in html

    def test_onboarding_has_managed_card(self, full_client):
        with patch('database.get_stats', return_value={'total_buildings': 0}):
            resp = full_client.get('/gemeinde/onboarding')
            html = resp.data.decode()
            assert '/gemeinde/anfrage' in html

    def test_onboarding_mentions_free_open_source(self, full_client):
        with patch('database.get_stats', return_value={'total_buildings': 0}):
            resp = full_client.get('/gemeinde/onboarding')
            html = resp.data.decode()
            assert 'open-source' in html.lower() or 'Open Source' in html or 'AGPL' in html

    def test_onboarding_no_data_sale_language(self, full_client):
        with patch('database.get_stats', return_value={'total_buildings': 0}):
            resp = full_client.get('/gemeinde/onboarding')
            html = resp.data.decode()
            lower = html.lower()
            assert 'daten verkaufen' not in lower
            assert 'data monetization' not in lower
            assert 'b2b' not in lower


class TestSliceB_AnfragePageGET:
    """GET /gemeinde/anfrage renders intake form."""

    def test_anfrage_returns_200(self, full_client):
        with patch('database.get_stats', return_value={'total_buildings': 0}):
            resp = full_client.get('/gemeinde/anfrage')
            assert resp.status_code == 200

    def test_anfrage_has_required_fields(self, full_client):
        with patch('database.get_stats', return_value={'total_buildings': 0}):
            resp = full_client.get('/gemeinde/anfrage')
            html = resp.data.decode()
            assert 'gemeinde_name' in html
            assert 'kanton' in html
            assert 'contact_name' in html
            assert 'email' in html

    def test_anfrage_has_optional_fields(self, full_client):
        with patch('database.get_stats', return_value={'total_buildings': 0}):
            resp = full_client.get('/gemeinde/anfrage')
            html = resp.data.decode()
            assert 'bfs_nummer' in html or 'bfs' in html.lower()
            assert 'message' in html or 'nachricht' in html.lower()


class TestSliceC_AnfragePOST:
    """POST /gemeinde/anfrage validates, emails, tracks."""

    VALID_PAYLOAD = {
        'gemeinde_name': 'Dietikon',
        'kanton': 'ZH',
        'contact_name': 'Maria Muster',
        'email': 'maria@dietikon.ch',
    }

    def test_post_success(self, full_client):
        with (
            patch('database.get_stats', return_value={'total_buildings': 0}),
            patch('municipality.email_utils.send_email', return_value=True) as mock_email,
            patch('municipality.db.track_event') as mock_track,
        ):
            resp = full_client.post('/gemeinde/anfrage', json=self.VALID_PAYLOAD, content_type='application/json')
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['success'] is True

    def test_post_sends_email(self, full_client):
        with (
            patch('database.get_stats', return_value={'total_buildings': 0}),
            patch('municipality.email_utils.send_email', return_value=True) as mock_email,
            patch('municipality.db.track_event'),
        ):
            full_client.post('/gemeinde/anfrage', json=self.VALID_PAYLOAD, content_type='application/json')
            assert mock_email.called

    def test_post_tracks_event(self, full_client):
        with (
            patch('database.get_stats', return_value={'total_buildings': 0}),
            patch('municipality.email_utils.send_email', return_value=True),
            patch('municipality.db.track_event') as mock_track,
        ):
            full_client.post('/gemeinde/anfrage', json=self.VALID_PAYLOAD, content_type='application/json')
            mock_track.assert_called_once()
            call_args = mock_track.call_args
            assert call_args[0][0] == 'gemeinde_anfrage'
            event_data = (
                call_args[1].get('data') or call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get('data')
            )
            assert event_data['gemeinde'] == 'Dietikon'
            assert event_data['kanton'] == 'ZH'

    def test_post_with_optional_fields(self, full_client):
        payload = {
            **self.VALID_PAYLOAD,
            'bfs_nummer': '261',
            'einwohner': '29000',
            'rolle': 'Gemeindeschreiberin',
            'phone': '+41 44 123 45 67',
            'dso': 'EKZ',
            'subdomain': 'dietikon',
            'message': 'Wir wollen starten.',
        }
        with (
            patch('database.get_stats', return_value={'total_buildings': 0}),
            patch('municipality.email_utils.send_email', return_value=True),
            patch('municipality.db.track_event'),
        ):
            resp = full_client.post('/gemeinde/anfrage', json=payload, content_type='application/json')
            assert resp.status_code == 200


class TestSliceD_ValidationErrors:
    """POST /gemeinde/anfrage returns 400 for bad input."""

    def test_missing_gemeinde_name(self, full_client):
        with patch('database.get_stats', return_value={'total_buildings': 0}):
            resp = full_client.post(
                '/gemeinde/anfrage',
                json={'kanton': 'ZH', 'contact_name': 'X', 'email': 'x@y.ch'},
                content_type='application/json',
            )
            assert resp.status_code == 400
            assert 'error' in resp.get_json()

    def test_missing_email(self, full_client):
        with patch('database.get_stats', return_value={'total_buildings': 0}):
            resp = full_client.post(
                '/gemeinde/anfrage',
                json={'gemeinde_name': 'Test', 'kanton': 'ZH', 'contact_name': 'X'},
                content_type='application/json',
            )
            assert resp.status_code == 400

    def test_invalid_email(self, full_client):
        with patch('database.get_stats', return_value={'total_buildings': 0}):
            resp = full_client.post(
                '/gemeinde/anfrage',
                json={'gemeinde_name': 'Test', 'kanton': 'ZH', 'contact_name': 'X', 'email': 'not-an-email'},
                content_type='application/json',
            )
            assert resp.status_code == 400

    def test_missing_kanton(self, full_client):
        with patch('database.get_stats', return_value={'total_buildings': 0}):
            resp = full_client.post(
                '/gemeinde/anfrage',
                json={'gemeinde_name': 'Test', 'contact_name': 'X', 'email': 'x@y.ch'},
                content_type='application/json',
            )
            assert resp.status_code == 400

    def test_empty_body(self, full_client):
        with patch('database.get_stats', return_value={'total_buildings': 0}):
            resp = full_client.post('/gemeinde/anfrage', json={}, content_type='application/json')
            assert resp.status_code == 400


class TestSliceE_Observability:
    """SMTP failure doesn't block success; event still tracked."""

    def test_email_failure_still_returns_success(self, full_client):
        with (
            patch('database.get_stats', return_value={'total_buildings': 0}),
            patch('municipality.email_utils.send_email', return_value=False),
            patch('municipality.db.track_event'),
        ):
            resp = full_client.post(
                '/gemeinde/anfrage',
                json={'gemeinde_name': 'Dietikon', 'kanton': 'ZH', 'contact_name': 'Maria', 'email': 'm@d.ch'},
                content_type='application/json',
            )
            assert resp.status_code == 200
            assert resp.get_json()['success'] is True

    def test_email_failure_still_tracks_event(self, full_client):
        with (
            patch('database.get_stats', return_value={'total_buildings': 0}),
            patch('municipality.email_utils.send_email', return_value=False),
            patch('municipality.db.track_event') as mock_track,
        ):
            full_client.post(
                '/gemeinde/anfrage',
                json={'gemeinde_name': 'Dietikon', 'kanton': 'ZH', 'contact_name': 'Maria', 'email': 'm@d.ch'},
                content_type='application/json',
            )
            mock_track.assert_called_once()


class TestSliceF_Regression:
    """Existing routes still work after onboarding rewrite."""

    def test_dashboard_still_works(self, full_client):
        with (
            patch('database.get_stats', return_value={'total_buildings': 0}),
            patch('municipality.db.get_municipality', return_value=None),
        ):
            resp = full_client.get('/gemeinde/dashboard')
            assert resp.status_code == 200

    def test_register_still_works(self, full_client):
        with (
            patch('database.get_stats', return_value={'total_buildings': 0}),
            patch('municipality.db.save_municipality', return_value=1),
            patch('municipality.db.update_municipality_status'),
            patch('municipality.db.track_event'),
        ):
            resp = full_client.post(
                '/gemeinde/register',
                json={'bfs_number': 261, 'name': 'Dietikon', 'admin_email': 'test@dietikon.ch'},
                content_type='application/json',
            )
            assert resp.status_code == 200

    def test_formation_still_works(self, full_client):
        with (
            patch('database.get_stats', return_value={'total_buildings': 0}),
            patch('municipality.db.get_municipality', return_value=None),
        ):
            resp = full_client.get('/gemeinde/formation')
            assert resp.status_code == 200
