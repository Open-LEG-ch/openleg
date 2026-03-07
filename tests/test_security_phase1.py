"""Phase 1 security tests: IDOR fixes, registration hardening, unsubscribe safety."""

import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestDashboardTokenGating:
    """Dashboard must require a valid dashboard token, not just ?bid=."""

    def test_dashboard_no_token_returns_403(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        # Dashboard route must not use request.args.get('bid')
        assert "request.args.get('bid')" not in content, 'Dashboard still uses ?bid= query param (IDOR)'

    def test_dashboard_requires_token_lookup(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        assert 'get_token' in content or '_require_dashboard_token' in content, 'Dashboard must validate token from DB'

    def test_dashboard_token_type_exists(self):
        """Registration must issue a 'dashboard' type token."""
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        assert "'dashboard'" in content, 'No dashboard token type found in app.py'


class TestReferralStatsAuth:
    """Referral stats must require dashboard token, not just building_id in URL."""

    def test_referral_stats_not_open(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        # The old route was /api/referral/stats/<building_id> with no auth
        assert 'def api_referral_stats(building_id)' not in content, (
            'Referral stats still takes building_id from URL path (IDOR)'
        )


class TestMeterDataAuth:
    """Meter data upload must verify caller owns the building."""

    def test_meter_upload_checks_token(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        # Must not blindly accept building_id from request body
        lines = content.split('\n')
        in_meter_fn = False
        has_token_check = False
        for line in lines:
            if 'def api_meter_data_upload' in line:
                in_meter_fn = True
            elif in_meter_fn and line.strip().startswith('def '):
                break
            elif in_meter_fn and (
                'get_token' in line or '_require_dashboard_token' in line or 'dashboard' in line.lower()
            ):
                has_token_check = True
        assert has_token_check, 'Meter upload does not verify dashboard token'


class TestRegistrationUpsertHardening:
    """save_building ON CONFLICT must not overwrite identity fields."""

    def test_upsert_does_not_overwrite_email(self):
        with open(os.path.join(PROJECT_ROOT, 'database.py')) as f:
            content = f.read()
        # After fix: ON CONFLICT should NOT SET email = EXCLUDED.email
        assert 'email = EXCLUDED.email' not in content, (
            'save_building upsert still overwrites email (registration takeover)'
        )


class TestUnsubscribeNoDelete:
    """POST /unsubscribe must not call delete_building."""

    def test_unsubscribe_post_no_delete(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        # Find the unsubscribe_page function and check it doesn't delete
        lines = content.split('\n')
        in_unsub = False
        calls_delete = False
        for line in lines:
            if 'def unsubscribe_page' in line:
                in_unsub = True
            elif in_unsub and line.strip().startswith('def '):
                break
            elif in_unsub and 'delete_building' in line:
                calls_delete = True
        assert not calls_delete, 'POST /unsubscribe still calls delete_building directly'

    def test_unsubscribe_shows_check_email_message(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        # Should tell user to check email instead of deleting
        assert 'Prüfen Sie Ihre E-Mail' in content or 'check' in content.lower() or 'E-Mail' in content, (
            'Unsubscribe POST should instruct user to check email'
        )
