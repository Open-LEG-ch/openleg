"""Phase 2 security tests: webhook HMAC, cron fail-closed, token leakage, schema types."""

import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestDeepSignWebhookHMAC:
    """DeepSign webhook must verify HMAC signature."""

    def test_webhook_has_hmac_verification(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        # Webhook route must verify signature before processing
        lines = content.split('\n')
        in_webhook = False
        has_verify = False
        for line in lines:
            if 'def webhook_deepsign' in line:
                in_webhook = True
            elif in_webhook and line.strip().startswith('def '):
                break
            elif in_webhook and ('verify' in line.lower() or 'hmac' in line.lower() or 'signature' in line.lower()):
                has_verify = True
        assert has_verify, 'DeepSign webhook does not verify signature'

    def test_deepsign_integration_has_verify_function(self):
        with open(os.path.join(PROJECT_ROOT, 'deepsign_integration.py')) as f:
            content = f.read()
        assert 'verify_webhook_signature' in content or 'verify_signature' in content, (
            'deepsign_integration.py missing signature verification function'
        )


class TestCronFailClosed:
    """Cron endpoints must abort(503) when CRON_SECRET is unconfigured."""

    def test_cron_emails_fail_closed(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        # Old pattern: `if CRON_SECRET and secret != CRON_SECRET` fails open when CRON_SECRET empty
        assert 'if CRON_SECRET and secret != CRON_SECRET' not in content, (
            'Cron process-emails still uses fail-open pattern'
        )

    def test_cron_refresh_fail_closed(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        assert '_require_cron_secret' in content, 'Cron endpoints should use _require_cron_secret helper'


class TestNoQueryStringTokens:
    """Admin and cron must not accept tokens/secrets via query params."""

    def test_admin_no_query_param_token(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        # Find _require_admin function
        lines = content.split('\n')
        in_admin = False
        has_args_get = False
        for line in lines:
            if 'def _require_admin' in line:
                in_admin = True
            elif in_admin and line.strip().startswith('def '):
                break
            elif in_admin and "request.args.get('token')" in line:
                has_args_get = True
        assert not has_args_get, '_require_admin still accepts token from query string'

    def test_utility_admin_no_query_param_token(self):
        with open(os.path.join(PROJECT_ROOT, 'utility_portal.py')) as f:
            content = f.read()
        # admin_clients should not accept token from query params
        lines = content.split('\n')
        in_admin = False
        has_args_token = False
        for line in lines:
            if 'def admin_clients' in line:
                in_admin = True
            elif in_admin and line.strip().startswith('def '):
                break
            elif in_admin and "request.args.get('token')" in line:
                has_args_token = True
        assert not has_args_token, 'utility admin_clients still accepts token from query string'


class TestCommunityIdType:
    """billing_periods, invoices, leg_documents community_id should be VARCHAR."""

    def test_billing_periods_community_id_varchar(self):
        with open(os.path.join(PROJECT_ROOT, 'database.py')) as f:
            content = f.read()
        # Check that billing_periods uses VARCHAR community_id
        assert (
            'community_id INTEGER NOT NULL' not in content.split('billing_periods')[1].split('CREATE TABLE')[0]
            if 'billing_periods' in content
            else True
        ), 'billing_periods still uses INTEGER community_id'

    def test_invoices_community_id_varchar(self):
        with open(os.path.join(PROJECT_ROOT, 'database.py')) as f:
            content = f.read()
        # After invoices table definition, community_id should be VARCHAR
        # Simplified check: no "community_id INTEGER" in those table defs
        lines = content.split('\n')
        in_invoices = False
        uses_integer = False
        for line in lines:
            if 'invoices' in line and 'CREATE TABLE' in line:
                in_invoices = True
            elif in_invoices and ')' in line and 'CREATE' not in line:
                in_invoices = False
            elif in_invoices and 'community_id INTEGER' in line:
                uses_integer = True
        assert not uses_integer, 'invoices table still uses INTEGER community_id'
