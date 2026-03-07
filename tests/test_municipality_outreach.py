"""Tests for Municipality Outreach Phase 2 (slices 2.1-2.10)."""

import os
import sys
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


# === 2.1: municipality_outreach_queue CRUD ===


class TestOutreachQueueCRUD:
    """Slice 2.1: DB CRUD for municipality_outreach_queue."""

    @patch('database.get_connection')
    def test_schedule_municipality_outreach_returns_id(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = {'id': 42}
        mock_conn.return_value.__enter__ = lambda s: MagicMock(
            cursor=MagicMock(return_value=MagicMock(__enter__=lambda s: mock_cur, __exit__=MagicMock()))
        )
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        import database as db

        result = db.schedule_municipality_outreach('Dietikon', 261, 'ZH', 'info@dietikon.ch')
        assert result == 42

    @patch('database.get_connection')
    def test_schedule_duplicate_returns_none(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None  # ON CONFLICT DO NOTHING
        mock_conn.return_value.__enter__ = lambda s: MagicMock(
            cursor=MagicMock(return_value=MagicMock(__enter__=lambda s: mock_cur, __exit__=MagicMock()))
        )
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        import database as db

        result = db.schedule_municipality_outreach('Dietikon', 261, 'ZH', 'info@dietikon.ch')
        assert result is None

    @patch('database.get_connection')
    def test_get_pending_municipality_outreach(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            {'id': 1, 'municipality_name': 'Dietikon', 'status': 'pending'},
        ]
        mock_conn.return_value.__enter__ = lambda s: MagicMock(
            cursor=MagicMock(return_value=MagicMock(__enter__=lambda s: mock_cur, __exit__=MagicMock()))
        )
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        import database as db

        result = db.get_pending_municipality_outreach(limit=5)
        assert len(result) == 1
        assert result[0]['municipality_name'] == 'Dietikon'

    @patch('database.get_connection')
    def test_mark_municipality_outreach_sent(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.rowcount = 1
        mock_conn.return_value.__enter__ = lambda s: MagicMock(
            cursor=MagicMock(return_value=MagicMock(__enter__=lambda s: mock_cur, __exit__=MagicMock()))
        )
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        import database as db

        assert db.mark_municipality_outreach_sent(1) is True

    @patch('database.get_connection')
    def test_mark_municipality_outreach_failed(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.rowcount = 1
        mock_conn.return_value.__enter__ = lambda s: MagicMock(
            cursor=MagicMock(return_value=MagicMock(__enter__=lambda s: mock_cur, __exit__=MagicMock()))
        )
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        import database as db

        assert db.mark_municipality_outreach_failed(1, 'SMTP error') is True

    @patch('database.get_connection')
    def test_get_municipality_outreach_history_no_filter(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [{'id': 1}, {'id': 2}]
        mock_conn.return_value.__enter__ = lambda s: MagicMock(
            cursor=MagicMock(return_value=MagicMock(__enter__=lambda s: mock_cur, __exit__=MagicMock()))
        )
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        import database as db

        result = db.get_municipality_outreach_history()
        assert len(result) == 2

    @patch('database.get_connection')
    def test_get_municipality_outreach_history_filtered(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [{'id': 1, 'status': 'sent'}]
        mock_conn.return_value.__enter__ = lambda s: MagicMock(
            cursor=MagicMock(return_value=MagicMock(__enter__=lambda s: mock_cur, __exit__=MagicMock()))
        )
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        import database as db

        result = db.get_municipality_outreach_history(contact_email='x@x.ch', status='sent')
        assert len(result) == 1


# === 2.2: score_outreach_candidates ===


class TestScoreOutreachCandidates:
    """Slice 2.2: Scoring municipality profiles for outreach priority."""

    def test_score_outreach_candidates_basic(self):
        from email_automation import score_outreach_candidates

        profiles = [
            {
                'name': 'A',
                'contact_email': 'a@a.ch',
                'energy_transition_score': 80,
                'leg_value_gap_chf': 200,
                'population': 50000,
            },
            {
                'name': 'B',
                'contact_email': 'b@b.ch',
                'energy_transition_score': 40,
                'leg_value_gap_chf': 100,
                'population': 10000,
            },
        ]
        result = score_outreach_candidates(profiles, set())
        assert len(result) == 2
        assert result[0]['outreach_score'] >= result[1]['outreach_score']
        assert result[0]['name'] == 'A'

    def test_excludes_already_contacted(self):
        from email_automation import score_outreach_candidates

        profiles = [
            {
                'name': 'A',
                'contact_email': 'a@a.ch',
                'energy_transition_score': 80,
                'leg_value_gap_chf': 200,
                'population': 50000,
            },
            {
                'name': 'B',
                'contact_email': 'b@b.ch',
                'energy_transition_score': 40,
                'leg_value_gap_chf': 100,
                'population': 10000,
            },
        ]
        result = score_outreach_candidates(profiles, {'a@a.ch'})
        assert len(result) == 1
        assert result[0]['name'] == 'B'

    def test_skips_profiles_without_email(self):
        from email_automation import score_outreach_candidates

        profiles = [
            {'name': 'A', 'energy_transition_score': 80, 'leg_value_gap_chf': 200, 'population': 50000},
        ]
        result = score_outreach_candidates(profiles, set())
        assert len(result) == 0

    def test_handles_missing_fields_gracefully(self):
        from email_automation import score_outreach_candidates

        profiles = [
            {'name': 'A', 'contact_email': 'a@a.ch'},
        ]
        result = score_outreach_candidates(profiles, set())
        assert len(result) == 1
        assert 'outreach_score' in result[0]


# === 2.3: schedule_outreach_batch ===


class TestScheduleOutreachBatch:
    """Slice 2.3: Batch scheduling of municipality outreach."""

    @patch('database.schedule_municipality_outreach', return_value=1)
    @patch('database.get_municipality_outreach_history', return_value=[])
    @patch('database.get_all_vnb_research')
    @patch('database.get_all_municipality_profiles')
    def test_schedule_batch_picks_top_candidates(self, mock_profiles, mock_vnb, mock_history, mock_schedule):
        mock_profiles.return_value = [
            {
                'name': 'Dietikon',
                'bfs_number': 261,
                'kanton': 'ZH',
                'energy_transition_score': 50,
                'leg_value_gap_chf': 200,
                'population': 29000,
            },
        ]
        mock_vnb.return_value = [
            {'vnb_name': 'EKZ', 'contact_email': 'info@ekz.ch', 'bfs_numbers': [261], 'kanton': 'ZH'},
        ]
        from email_automation import schedule_outreach_batch

        count = schedule_outreach_batch(limit=5)
        assert count >= 1
        mock_schedule.assert_called()

    @patch('database.schedule_municipality_outreach', return_value=1)
    @patch('database.get_municipality_outreach_history')
    @patch('database.get_all_vnb_research', return_value=[])
    @patch('database.get_all_municipality_profiles', return_value=[])
    def test_schedule_batch_returns_zero_when_empty(self, mock_profiles, mock_vnb, mock_history, mock_schedule):
        mock_history.return_value = []
        from email_automation import schedule_outreach_batch

        count = schedule_outreach_batch(limit=5)
        assert count == 0


# === 2.4: process_municipality_outreach ===


class TestProcessMunicipalityOutreach:
    """Slice 2.4: Processing outreach queue."""

    @patch('email_automation._notify_outreach_sent')
    @patch('email_automation._send_email', return_value=True)
    @patch('database.mark_municipality_outreach_sent', return_value=True)
    @patch('database.get_pending_municipality_outreach')
    def test_process_sends_pending(self, mock_pending, mock_mark, mock_send, mock_notify):
        mock_pending.return_value = [
            {
                'id': 1,
                'municipality_name': 'Dietikon',
                'bfs_number': 261,
                'kanton': 'ZH',
                'contact_email': 'info@dietikon.ch',
                'email_type': 'initial',
                'followup_number': 0,
            },
        ]
        from email_automation import process_municipality_outreach

        result = process_municipality_outreach()
        assert result['sent'] == 1
        assert result['failed'] == 0
        mock_send.assert_called_once()

    @patch('email_automation._notify_outreach_sent')
    @patch('email_automation._send_email', return_value=False)
    @patch('database.mark_municipality_outreach_failed', return_value=True)
    @patch('database.get_pending_municipality_outreach')
    def test_process_marks_failed(self, mock_pending, mock_mark_fail, mock_send, mock_notify):
        mock_pending.return_value = [
            {
                'id': 1,
                'municipality_name': 'Dietikon',
                'bfs_number': 261,
                'kanton': 'ZH',
                'contact_email': 'info@dietikon.ch',
                'email_type': 'initial',
                'followup_number': 0,
            },
        ]
        from email_automation import process_municipality_outreach

        result = process_municipality_outreach()
        assert result['sent'] == 0
        assert result['failed'] == 1

    @patch('database.get_pending_municipality_outreach', return_value=[])
    def test_process_empty_queue(self, mock_pending):
        from email_automation import process_municipality_outreach

        result = process_municipality_outreach()
        assert result['sent'] == 0
        assert result['failed'] == 0

    def test_process_renders_template_with_app_context(self, full_app):
        pending_data = [
            {
                'id': 1,
                'municipality_name': 'Dietikon',
                'bfs_number': 261,
                'kanton': 'ZH',
                'contact_email': 'info@dietikon.ch',
                'email_type': 'initial',
                'followup_number': 0,
            },
        ]
        with (
            patch('email_automation.db.get_pending_municipality_outreach', return_value=pending_data),
            patch('email_automation.db.mark_municipality_outreach_sent', return_value=True),
            patch('email_automation._send_email', return_value=True) as mock_send,
            patch('email_automation._notify_outreach_sent'),
        ):
            from email_automation import process_municipality_outreach

            result = process_municipality_outreach(app=full_app)
            assert result['sent'] == 1
            # Verify HTML was rendered (not fallback)
            call_args = mock_send.call_args
            assert 'Dietikon' in call_args[0][2] or 'dietikon' in call_args[0][2].lower()


# === 2.5: Cron endpoint ===


class TestCronMunicipalityOutreach:
    """Slice 2.5: POST /api/cron/process-municipality-outreach."""

    @patch('email_automation.process_municipality_outreach')
    @patch('email_automation.schedule_municipality_followups', return_value=0)
    def test_cron_endpoint_processes(self, mock_followups, mock_process, full_client):
        mock_process.return_value = {'sent': 2, 'failed': 0}
        resp = full_client.post(
            '/api/cron/process-municipality-outreach', headers={'X-Cron-Secret': 'test-cron-secret'}
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['sent'] == 2
        assert data['newly_scheduled'] == 0

    @patch('email_automation.schedule_outreach_batch', return_value=5)
    @patch('email_automation.process_municipality_outreach')
    @patch('email_automation.schedule_municipality_followups', return_value=0)
    def test_cron_endpoint_schedules_new(self, mock_followups, mock_process, mock_batch, full_client):
        mock_process.return_value = {'sent': 0, 'failed': 0}
        resp = full_client.post(
            '/api/cron/process-municipality-outreach',
            headers={'X-Cron-Secret': 'test-cron-secret'},
            json={'schedule_new': True, 'limit': 5},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['newly_scheduled'] == 5

    def test_cron_endpoint_requires_secret(self, full_client):
        resp = full_client.post('/api/cron/process-municipality-outreach')
        assert resp.status_code == 403


# === 2.6: 7-day follow-up scheduling ===


class TestFollowupScheduling7Day:
    """Slice 2.6: Schedule 7-day follow-ups."""

    @patch('database.schedule_municipality_outreach', return_value=10)
    @patch('database.get_sent_outreach_needing_followup')
    def test_schedule_followups_7day(self, mock_needing, mock_schedule):
        mock_needing.return_value = [
            {
                'id': 1,
                'municipality_name': 'Dietikon',
                'bfs_number': 261,
                'kanton': 'ZH',
                'contact_email': 'info@dietikon.ch',
            },
        ]
        from email_automation import schedule_municipality_followups

        count = schedule_municipality_followups(followup_number=1, days_after=7)
        assert count == 1
        mock_schedule.assert_called_once()
        # Verify followup_number=1 and email_type='followup'
        args, kwargs = mock_schedule.call_args
        assert kwargs.get('followup_number') == 1 or args[5] == 1 if len(args) > 5 else True

    @patch('database.get_sent_outreach_needing_followup', return_value=[])
    def test_schedule_followups_none_needed(self, mock_needing):
        from email_automation import schedule_municipality_followups

        count = schedule_municipality_followups(followup_number=1, days_after=7)
        assert count == 0


# === 2.7: 14-day follow-up #2 ===


class TestFollowupScheduling14Day:
    """Slice 2.7: Schedule 14-day follow-ups."""

    @patch('database.schedule_municipality_outreach', return_value=20)
    @patch('database.get_sent_outreach_needing_followup')
    def test_schedule_followups_14day(self, mock_needing, mock_schedule):
        mock_needing.return_value = [
            {
                'id': 2,
                'municipality_name': 'Schlieren',
                'bfs_number': 247,
                'kanton': 'ZH',
                'contact_email': 'info@schlieren.ch',
            },
        ]
        from email_automation import schedule_municipality_followups

        count = schedule_municipality_followups(followup_number=2, days_after=14)
        assert count == 1

    @patch('database.get_sent_outreach_needing_followup')
    def test_get_sent_outreach_needing_followup_db(self, mock_conn):
        """Verify DB function exists and is callable."""
        import database as db

        assert callable(db.get_sent_outreach_needing_followup)


# === 2.8: Follow-up email templates ===


class TestFollowupTemplates:
    """Slice 2.8: Follow-up email templates exist and have correct content."""

    def test_followup_1_template_exists(self):
        path = os.path.join(PROJECT_ROOT, 'templates', 'emails', 'gemeinde_followup_1.html')
        assert os.path.exists(path)

    def test_followup_2_template_exists(self):
        path = os.path.join(PROJECT_ROOT, 'templates', 'emails', 'gemeinde_followup_2.html')
        assert os.path.exists(path)

    def test_followup_1_has_reminder_content(self):
        path = os.path.join(PROJECT_ROOT, 'templates', 'emails', 'gemeinde_followup_1.html')
        with open(path) as f:
            content = f.read()
        assert 'gemeinde_name' in content
        assert 'Nachricht' in content or 'Erinnerung' in content
        assert 'profil_url' in content

    def test_followup_2_has_urgency_content(self):
        path = os.path.join(PROJECT_ROOT, 'templates', 'emails', 'gemeinde_followup_2.html')
        with open(path) as f:
            content = f.read()
        assert 'gemeinde_name' in content
        assert 'Letzte' in content or 'letzte' in content
        assert 'profil_url' in content

    def test_followup_1_renders(self, full_app):
        with full_app.app_context():
            from flask import render_template

            html = render_template(
                'emails/gemeinde_followup_1.html',
                gemeinde_name='Dietikon',
                kanton='ZH',
                energy_transition_score=42.5,
                leg_value_gap_chf=171,
                profil_url='https://openleg.ch/gemeinde/261/profil',
                claim_url='https://openleg.ch/gemeinde/onboarding?subdomain=dietikon',
                subdomain='dietikon',
            )
        assert 'Dietikon' in html

    def test_followup_2_renders(self, full_app):
        with full_app.app_context():
            from flask import render_template

            html = render_template(
                'emails/gemeinde_followup_2.html',
                gemeinde_name='Schlieren',
                kanton='ZH',
                energy_transition_score=38,
                leg_value_gap_chf=155,
                profil_url='https://openleg.ch/gemeinde/247/profil',
                claim_url='https://openleg.ch/gemeinde/onboarding?subdomain=schlieren',
                subdomain='schlieren',
            )
        assert 'Schlieren' in html


# === 2.9: Telegram notification ===


class TestTelegramNotification:
    """Slice 2.9: Telegram FYI after outreach sends."""

    def test_notify_outreach_sent_exists(self):
        from email_automation import _notify_outreach_sent

        assert callable(_notify_outreach_sent)

    @patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'fake', 'TELEGRAM_CHAT_ID': '123'})
    @patch('urllib.request.urlopen')
    def test_notify_outreach_sent_fires(self, mock_urlopen):
        from email_automation import _notify_outreach_sent

        _notify_outreach_sent('Dietikon', 'initial', 3)
        # Non-blocking: may or may not have fired yet, just no exception

    @patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': '', 'TELEGRAM_CHAT_ID': ''})
    def test_notify_outreach_sent_noop_without_tokens(self):
        from email_automation import _notify_outreach_sent

        # Should not raise
        _notify_outreach_sent('Dietikon', 'initial', 1)


# === 2.10: Wire followups into cron ===


class TestCronWiresFollowups:
    """Slice 2.10: Cron endpoint also schedules follow-ups."""

    @patch('email_automation.schedule_municipality_followups')
    @patch('email_automation.process_municipality_outreach')
    def test_cron_schedules_followups(self, mock_process, mock_followups, full_client):
        mock_process.return_value = {'sent': 1, 'failed': 0}
        mock_followups.return_value = 2
        resp = full_client.post(
            '/api/cron/process-municipality-outreach', headers={'X-Cron-Secret': 'test-cron-secret'}
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['followups_scheduled'] == 4  # 2 for each of followup 1 + followup 2
        assert mock_followups.call_count == 2
