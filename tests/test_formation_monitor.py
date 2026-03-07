"""Tests for formation pipeline monitor + strategy dashboard (P2)."""

from unittest.mock import MagicMock, patch

MOCK_PIPELINE_STATS = {
    'interested': 5,
    'formation_started': 3,
    'active': 2,
    'dissolved': 1,
}

MOCK_STUCK = [
    {'community_id': 'c1', 'name': 'Solar Dietikon', 'confirmed_count': 4, 'member_emails': ['a@b.ch']},
]


class TestPipelineStats:
    """database.get_formation_pipeline_stats returns grouped counts."""

    @patch('database.get_connection')
    def test_pipeline_stats_returns_by_status(self, mock_conn):
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {'status': 'interested', 'count': 5},
            {'status': 'formation_started', 'count': 3},
            {'status': 'active', 'count': 2},
        ]
        mock_cursor.__enter__ = lambda s: s
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_connection = MagicMock()
        mock_connection.cursor.return_value = mock_cursor
        mock_connection.__enter__ = lambda s: s
        mock_connection.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value = mock_connection

        from database import get_formation_pipeline_stats

        result = get_formation_pipeline_stats()
        assert result['interested'] == 5
        assert result['formation_started'] == 3
        assert result['active'] == 2


class TestMonitorFormationPipeline:
    """email_automation.monitor_formation_pipeline returns summary."""

    @patch('email_automation.check_formation_ready_communities', return_value=MOCK_STUCK)
    @patch('database.get_formation_pipeline_stats', return_value=MOCK_PIPELINE_STATS)
    def test_monitor_returns_summary(self, mock_stats, mock_stuck):
        from email_automation import monitor_formation_pipeline

        result = monitor_formation_pipeline()
        assert 'by_status' in result
        assert 'stuck' in result
        assert 'total_communities' in result
        assert result['total_communities'] == 11
        assert result['stuck'] == MOCK_STUCK


class TestMonitorCronEndpoint:
    """Cron endpoint for pipeline monitoring."""

    def test_monitor_cron_requires_secret(self, full_client):
        resp = full_client.post('/api/cron/monitor-formations')
        assert resp.status_code == 403

    @patch('email_automation.send_formation_nudge', return_value=1)
    @patch(
        'email_automation.monitor_formation_pipeline',
        return_value={
            'by_status': MOCK_PIPELINE_STATS,
            'stuck': MOCK_STUCK,
            'total_communities': 11,
            'healthy': True,
        },
    )
    @patch('database.track_event', return_value=True)
    def test_monitor_cron_sends_nudges(self, mock_track, mock_monitor, mock_nudge, full_client):
        resp = full_client.post('/api/cron/monitor-formations', headers={'X-Cron-Secret': 'test-cron-secret'})
        assert resp.status_code == 200
        mock_nudge.assert_called_once()

    @patch('email_automation.send_formation_nudge', return_value=1)
    @patch(
        'email_automation.monitor_formation_pipeline',
        return_value={
            'by_status': MOCK_PIPELINE_STATS,
            'stuck': MOCK_STUCK,
            'total_communities': 11,
            'healthy': True,
        },
    )
    @patch('database.track_event', return_value=True)
    def test_monitor_cron_tracks_event(self, mock_track, mock_monitor, mock_nudge, full_client):
        full_client.post('/api/cron/monitor-formations', headers={'X-Cron-Secret': 'test-cron-secret'})
        mock_track.assert_called_once()
        assert mock_track.call_args[0][0] == 'pipeline_monitor_run'


class TestAdminStrategy:
    """Admin strategy dashboard."""

    def test_admin_strategy_requires_auth(self, full_client):
        resp = full_client.get('/admin/strategy')
        assert resp.status_code == 403

    @patch(
        'email_automation.monitor_formation_pipeline',
        return_value={
            'by_status': MOCK_PIPELINE_STATS,
            'stuck': MOCK_STUCK,
            'total_communities': 11,
            'healthy': True,
        },
    )
    @patch('database.get_email_stats', return_value={'pending': 5, 'sent': 100})
    @patch('database.get_stats', return_value={'total_buildings': 50, 'registrations_today': 3})
    def test_admin_strategy_returns_data(self, mock_stats, mock_email, mock_monitor, full_client):
        resp = full_client.get('/admin/strategy', headers={'X-Admin-Token': 'test-admin-token'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'stats' in data
        assert 'pipeline' in data
        assert 'email_stats' in data

    @patch(
        'email_automation.monitor_formation_pipeline',
        return_value={
            'by_status': MOCK_PIPELINE_STATS,
            'stuck': MOCK_STUCK,
            'total_communities': 11,
            'healthy': True,
        },
    )
    @patch('database.get_email_stats', return_value={'pending': 5, 'sent': 100})
    @patch('database.get_stats', return_value={'total_buildings': 50, 'registrations_today': 3})
    def test_admin_strategy_html(self, mock_stats, mock_email, mock_monitor, full_client):
        resp = full_client.get('/admin/strategy', headers={'X-Admin-Token': 'test-admin-token', 'Accept': 'text/html'})
        assert resp.status_code == 200
        assert b'<!DOCTYPE html>' in resp.data or b'<html' in resp.data
