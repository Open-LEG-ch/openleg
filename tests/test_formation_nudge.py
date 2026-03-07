"""Tests for formation nudge email automation."""

import os
from unittest.mock import patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestFormationNudgeTemplate:
    def test_formation_nudge_template_exists(self):
        path = os.path.join(PROJECT_ROOT, 'templates', 'emails', 'formation_nudge.html')
        assert os.path.exists(path)

    def test_template_has_german_content(self):
        path = os.path.join(PROJECT_ROOT, 'templates', 'emails', 'formation_nudge.html')
        with open(path) as f:
            content = f.read()
        assert 'LEG-Gründung wartet' in content
        assert 'community_name' in content
        assert 'days_stuck' in content


class TestFormationNudgeRegistration:
    def test_formation_nudge_in_email_module(self):
        import email_automation

        assert 'formation_nudge' in email_automation.TRIGGER_TEMPLATES
        config = email_automation.TRIGGER_TEMPLATES['formation_nudge']
        assert 'subject' in config
        assert 'template' in config


class TestSendFormationNudge:
    def test_send_formation_nudge_exists(self):
        from email_automation import send_formation_nudge

        assert callable(send_formation_nudge)

    @patch('email_automation._send_email', return_value=True)
    def test_nudge_sends_to_confirmed_members(self, mock_send):
        from email_automation import send_formation_nudge

        emails = ['a@x.ch', 'b@x.ch', 'c@x.ch']
        sent = send_formation_nudge('c1', 'Test LEG', emails)
        assert sent == 3
        assert mock_send.call_count == 3


class TestCheckReadyCommunities:
    def test_check_ready_communities_exists(self):
        from email_automation import check_formation_ready_communities

        assert callable(check_formation_ready_communities)

    @patch('database.get_active_communities_for_nudge')
    def test_check_ready_returns_eligible(self, mock_db):
        mock_db.return_value = [
            {
                'community_id': 'c1',
                'name': 'LEG Dietikon',
                'member_emails': ['a@x.ch', 'b@x.ch', 'c@x.ch'],
                'confirmed_count': 3,
            },
        ]
        from email_automation import check_formation_ready_communities

        result = check_formation_ready_communities(min_members=3)
        assert len(result) == 1
        assert result[0]['confirmed_count'] >= 3


class TestEmailCronNudges:
    @patch('email_automation.send_formation_nudge', return_value=3)
    @patch('email_automation.check_formation_ready_communities')
    @patch('database.track_event')
    @patch('database.expire_stale_ceo_decisions')
    @patch('database.get_pending_emails', return_value=[])
    def test_email_cron_triggers_nudges(
        self, mock_pending, mock_expire, mock_track, mock_check, mock_nudge, full_client
    ):
        mock_check.return_value = [
            {'community_id': 'c1', 'name': 'Test LEG', 'member_emails': ['a@x.ch', 'b@x.ch', 'c@x.ch']},
        ]
        resp = full_client.post('/api/cron/process-emails', headers={'X-Cron-Secret': 'test-cron-secret'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['nudges_sent'] == 1
        mock_nudge.assert_called_once()
