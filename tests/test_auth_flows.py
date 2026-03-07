"""Behavioral tests for auth flows: dashboard, meter upload, unsubscribe, cron, webhook, register."""

import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestDashboardAuthFlow:
    """Dashboard requires valid dashboard token to access user data."""

    def test_no_bid_param_in_dashboard(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        # No ?bid= pattern in dashboard function
        lines = content.split('\n')
        in_dash = False
        for line in lines:
            if 'def dashboard(' in line:
                in_dash = True
            elif in_dash and line.strip().startswith('def '):
                break
            elif in_dash:
                assert "request.args.get('bid'" not in line, 'Dashboard still accepts building_id via query param'

    def test_dashboard_calls_require_token(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        lines = content.split('\n')
        in_dash = False
        has_token_check = False
        for line in lines:
            if 'def dashboard(' in line:
                in_dash = True
            elif in_dash and line.strip().startswith('def '):
                break
            elif in_dash and '_require_dashboard_token' in line:
                has_token_check = True
        assert has_token_check


class TestMeterUploadAuthFlow:
    """Meter upload gated by dashboard token, not arbitrary building_id."""

    def test_meter_upload_uses_dashboard_token(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        lines = content.split('\n')
        in_fn = False
        has_token = False
        for line in lines:
            if 'def api_meter_data_upload' in line:
                in_fn = True
            elif in_fn and line.strip().startswith('def '):
                break
            elif in_fn and '_require_dashboard_token' in line:
                has_token = True
        assert has_token, 'Meter upload must use _require_dashboard_token'

    def test_meter_upload_no_body_building_id(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        lines = content.split('\n')
        in_fn = False
        for line in lines:
            if 'def api_meter_data_upload' in line:
                in_fn = True
            elif in_fn and line.strip().startswith('def '):
                break
            elif in_fn:
                assert "data.get('building_id'" not in line, 'Meter upload still reads building_id from request body'


class TestUnsubscribeFlow:
    """POST /unsubscribe sends email link; GET /unsubscribe/<token> deletes."""

    def test_post_unsubscribe_sends_email(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        lines = content.split('\n')
        in_fn = False
        sends_email = False
        for line in lines:
            if 'def unsubscribe_page' in line:
                in_fn = True
            elif in_fn and line.strip().startswith('def '):
                break
            elif in_fn and 'send_email' in line:
                sends_email = True
        assert sends_email, 'POST /unsubscribe should send email with unsubscribe link'

    def test_token_unsubscribe_still_deletes(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        lines = content.split('\n')
        in_fn = False
        deletes = False
        for line in lines:
            if 'def unsubscribe_token' in line:
                in_fn = True
            elif in_fn and line.strip().startswith('def '):
                break
            elif in_fn and 'delete_building' in line:
                deletes = True
        assert deletes, 'GET /unsubscribe/<token> should still delete building'


class TestCronAuthFlow:
    """Cron endpoints use _require_cron_secret, fail-closed."""

    def test_all_cron_use_helper(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        cron_fns = ['api_cron_process_emails', 'api_cron_refresh_public_data', 'api_cron_process_billing']
        for fn_name in cron_fns:
            lines = content.split('\n')
            in_fn = False
            has_helper = False
            for line in lines:
                if f'def {fn_name}' in line:
                    in_fn = True
                elif in_fn and line.strip().startswith('def '):
                    break
                elif in_fn and '_require_cron_secret' in line:
                    has_helper = True
            assert has_helper, f'{fn_name} does not use _require_cron_secret'

    def test_require_cron_secret_aborts_503(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        lines = content.split('\n')
        in_fn = False
        has_503 = False
        for line in lines:
            if 'def _require_cron_secret' in line:
                in_fn = True
            elif in_fn and line.strip().startswith('def '):
                break
            elif in_fn and '503' in line:
                has_503 = True
        assert has_503, '_require_cron_secret must abort(503) when unconfigured'


class TestWebhookAuthFlow:
    """DeepSign webhook verifies HMAC before processing."""

    def test_webhook_verifies_before_handle(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        lines = content.split('\n')
        in_fn = False
        verify_line = None
        handle_line = None
        for i, line in enumerate(lines):
            if 'def webhook_deepsign' in line:
                in_fn = True
            elif in_fn and line.strip().startswith('def '):
                break
            elif in_fn and 'verify' in line.lower():
                verify_line = i
            elif in_fn and 'handle_webhook' in line:
                handle_line = i
        assert verify_line is not None, 'Webhook does not verify signature'
        assert handle_line is not None, 'Webhook does not call handle_webhook'
        assert verify_line < handle_line, 'Verification must happen before handling'


class TestRegistrationTokenFlow:
    """Registration endpoints issue dashboard tokens."""

    def test_register_anonymous_issues_dashboard_token(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        lines = content.split('\n')
        in_fn = False
        has_dashboard_token = False
        for line in lines:
            if 'def api_register_anonymous' in line:
                in_fn = True
            elif in_fn and line.strip().startswith('def '):
                break
            elif in_fn and "'dashboard'" in line:
                has_dashboard_token = True
        assert has_dashboard_token

    def test_register_full_issues_dashboard_token(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        lines = content.split('\n')
        in_fn = False
        has_dashboard_token = False
        for line in lines:
            if 'def api_register_full' in line:
                in_fn = True
            elif in_fn and line.strip().startswith('def '):
                break
            elif in_fn and "'dashboard'" in line:
                has_dashboard_token = True
        assert has_dashboard_token


class TestDeadCodeRemoved:
    """Dead files should be deleted."""

    def test_no_token_persistence(self):
        assert not os.path.exists(os.path.join(PROJECT_ROOT, 'token_persistence.py'))

    def test_no_stripe_integration(self):
        assert not os.path.exists(os.path.join(PROJECT_ROOT, 'stripe_integration.py'))

    def test_no_stripe_in_requirements(self):
        with open(os.path.join(PROJECT_ROOT, 'requirements.txt')) as f:
            content = f.read()
        assert 'stripe' not in content.lower()
