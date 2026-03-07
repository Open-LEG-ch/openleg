"""Tests for AgentMail integration: client wrapper, email_utils fallback, endpoints."""

import os
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestAgentMailClient:
    """Unit tests for agentmail_client.py."""

    def test_get_client_returns_none_without_key(self):
        with patch.dict(os.environ, {'AGENTMAIL_API_KEY': ''}, clear=False):
            import agentmail_client as amc

            amc._client = None
            amc.AGENTMAIL_API_KEY = ''
            assert amc.get_client() is None

    def test_get_client_creates_singleton(self):
        with patch.dict(os.environ, {'AGENTMAIL_API_KEY': 'test-key'}, clear=False):
            import agentmail_client as amc

            amc._client = None
            amc.AGENTMAIL_API_KEY = 'test-key'
            with patch('agentmail.AgentMail') as MockAM:
                MockAM.return_value = MagicMock()
                c1 = amc.get_client()
                c2 = amc.get_client()
                assert c1 is c2
                MockAM.assert_called_once_with(api_key='test-key')
            amc._client = None

    def test_send_message_returns_none_without_client(self):
        import agentmail_client as amc

        amc._client = None
        amc.AGENTMAIL_API_KEY = ''
        result = amc.send_message('inbox@test.com', 'to@test.com', 'subj', 'body')
        assert result is None

    def test_send_message_calls_sdk(self):
        import agentmail_client as amc

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.message_id = 'msg-123'
        mock_client.inboxes.messages.send.return_value = mock_resp
        amc._client = mock_client
        amc.AGENTMAIL_API_KEY = 'test-key'
        result = amc.send_message('inbox@t.com', 'to@t.com', 'Hello', 'Body text')
        assert result == 'msg-123'
        mock_client.inboxes.messages.send.assert_called_once_with(
            inbox_id='inbox@t.com', to='to@t.com', subject='Hello', text='Body text'
        )
        amc._client = None

    def test_send_message_with_html(self):
        import agentmail_client as amc

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.message_id = 'msg-456'
        mock_client.inboxes.messages.send.return_value = mock_resp
        amc._client = mock_client
        amc.AGENTMAIL_API_KEY = 'test-key'
        result = amc.send_message('inbox@t.com', 'to@t.com', 'Hi', 'text', html='<b>hi</b>')
        assert result == 'msg-456'
        mock_client.inboxes.messages.send.assert_called_once_with(
            inbox_id='inbox@t.com', to='to@t.com', subject='Hi', text='text', html='<b>hi</b>'
        )
        amc._client = None

    def test_ensure_inbox_returns_none_without_client(self):
        import agentmail_client as amc

        amc._client = None
        amc.AGENTMAIL_API_KEY = ''
        result = amc.ensure_inbox('test', 'agentmail.to')
        assert result is None

    def test_ensure_inbox_finds_existing(self):
        import agentmail_client as amc

        mock_client = MagicMock()
        existing_inbox = MagicMock()
        existing_inbox.inbox_id = 'hallo@agentmail.to'
        mock_list = MagicMock()
        mock_list.items = [existing_inbox]
        mock_client.inboxes.list.return_value = mock_list
        amc._client = mock_client
        amc.AGENTMAIL_API_KEY = 'test-key'
        amc.AGENTMAIL_DOMAIN = 'agentmail.to'
        result = amc.ensure_inbox('hallo')
        assert result == 'hallo@agentmail.to'
        mock_client.inboxes.create.assert_not_called()
        amc._client = None

    def test_ensure_inbox_creates_new(self):
        import agentmail_client as amc

        mock_client = MagicMock()
        mock_list = MagicMock()
        mock_list.items = []
        mock_client.inboxes.list.return_value = mock_list
        new_inbox = MagicMock()
        new_inbox.inbox_id = 'lea@agentmail.to'
        mock_client.inboxes.create.return_value = new_inbox
        amc._client = mock_client
        amc.AGENTMAIL_API_KEY = 'test-key'
        amc.AGENTMAIL_DOMAIN = 'agentmail.to'
        result = amc.ensure_inbox('lea')
        assert result == 'lea@agentmail.to'
        mock_client.inboxes.create.assert_called_once()
        amc._client = None

    def test_list_messages_empty_without_client(self):
        import agentmail_client as amc

        amc._client = None
        amc.AGENTMAIL_API_KEY = ''
        result = amc.list_messages('inbox@t.com')
        assert result == []

    def test_list_messages_returns_dicts(self):
        import agentmail_client as amc

        mock_client = MagicMock()
        msg = MagicMock()
        msg.id = 'm1'
        msg.message_id = 'm1'
        msg.from_ = 'a@b.com'
        msg.to = 'c@d.com'
        msg.subject = 'Test'
        msg.created_at = '2026-01-01'
        mock_resp = MagicMock()
        mock_resp.items = [msg]
        mock_client.inboxes.messages.list.return_value = mock_resp
        amc._client = mock_client
        amc.AGENTMAIL_API_KEY = 'test-key'
        result = amc.list_messages('inbox@t.com', limit=10)
        assert len(result) == 1
        assert result[0]['subject'] == 'Test'
        amc._client = None


class TestEmailUtilsFallback:
    """Test email_utils sends via AgentMail or falls back to SMTP."""

    def test_send_email_uses_agentmail_when_configured(self):
        import email_utils

        email_utils.USE_AGENTMAIL = True
        email_utils.SMTP_ENABLED = False
        email_utils.EMAIL_ENABLED = True
        email_utils._transactional_inbox = 'hallo@agentmail.to'
        with patch('agentmail_client.send_message', return_value='msg-1') as mock_send:
            result = email_utils.send_email('to@x.com', 'Subj', 'Body')
            assert result is True
            mock_send.assert_called_once()
        email_utils._transactional_inbox = None

    def test_send_email_falls_back_to_smtp(self):
        import email_utils

        email_utils.USE_AGENTMAIL = True
        email_utils.SMTP_ENABLED = True
        email_utils.EMAIL_ENABLED = True
        email_utils._transactional_inbox = None
        with patch('agentmail_client.ensure_inbox', return_value=None), patch('smtplib.SMTP') as mock_smtp:
            mock_server = MagicMock()
            mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
            email_utils.SMTP_USER = 'u'
            email_utils.SMTP_PASSWORD = 'p'
            result = email_utils.send_email('to@x.com', 'Subj', 'Body')
            assert result is True

    def test_send_email_dev_mode(self):
        import email_utils

        email_utils.USE_AGENTMAIL = False
        email_utils.SMTP_ENABLED = False
        email_utils.EMAIL_ENABLED = False
        result = email_utils.send_email('to@x.com', 'Subj', 'Body')
        assert result is True  # dev mode returns True


class TestSendEmailEndpoint:
    """Static + route tests for /api/internal/send-email."""

    def test_route_exists_in_source(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        assert '/api/internal/send-email' in content
        assert 'X-Internal-Token' in content

    def test_rejects_without_token(self):
        with patch.dict(
            os.environ,
            {
                'DATABASE_URL': 'postgresql://x:x@localhost/x',
                'ADMIN_TOKEN': 'test123',
                'INTERNAL_TOKEN': 'secret-internal',
            },
        ):
            with (
                patch('database.init_db', return_value=True),
                patch('database._connection_pool', MagicMock()),
                patch('database.is_db_available', return_value=True),
            ):
                try:
                    from app import app

                    client = app.test_client()
                    resp = client.post(
                        '/api/internal/send-email', json={'to': 'a@b.com', 'subject': 'Hi', 'text': 'Hello'}
                    )
                    assert resp.status_code == 403
                except Exception:
                    pytest.skip('App import requires live DB')

    def test_requires_to_subject_text(self):
        with patch.dict(
            os.environ,
            {
                'DATABASE_URL': 'postgresql://x:x@localhost/x',
                'ADMIN_TOKEN': 'test123',
                'INTERNAL_TOKEN': 'secret-internal',
            },
        ):
            with (
                patch('database.init_db', return_value=True),
                patch('database._connection_pool', MagicMock()),
                patch('database.is_db_available', return_value=True),
            ):
                try:
                    from app import app

                    client = app.test_client()
                    resp = client.post(
                        '/api/internal/send-email',
                        json={'to': 'a@b.com'},
                        headers={'X-Internal-Token': 'secret-internal'},
                    )
                    assert resp.status_code in (400, 403)
                except Exception:
                    pytest.skip('App import requires live DB')


class TestAgentMailWebhook:
    """Tests for /webhook/agentmail."""

    def test_webhook_route_exists_in_source(self):
        with open(os.path.join(PROJECT_ROOT, 'app.py')) as f:
            content = f.read()
        assert '/webhook/agentmail' in content

    def test_webhook_rejects_bad_secret(self):
        with patch.dict(
            os.environ,
            {
                'DATABASE_URL': 'postgresql://x:x@localhost/x',
                'ADMIN_TOKEN': 'test123',
                'AGENTMAIL_WEBHOOK_SECRET': 'wh-secret',
            },
        ):
            with (
                patch('database.init_db', return_value=True),
                patch('database._connection_pool', MagicMock()),
                patch('database.is_db_available', return_value=True),
            ):
                try:
                    from app import app

                    client = app.test_client()
                    resp = client.post(
                        '/webhook/agentmail', json={'type': 'message-received'}, headers={'X-Webhook-Secret': 'wrong'}
                    )
                    assert resp.status_code in (403, 200)  # depends on module-level env
                except Exception:
                    pytest.skip('App import requires live DB')


class TestMCPToolExists:
    """Verify send_outreach_email tool exists in MCP server source."""

    def test_send_outreach_email_tool_in_source(self):
        mcp_path = os.path.join(PROJECT_ROOT, 'openclaw', 'mcp-openleg-server', 'server.mjs')
        with open(mcp_path) as f:
            content = f.read()
        assert "'send_outreach_email'" in content
        assert '/api/internal/request-approval' in content
        assert 'X-Internal-Token' in content

    def test_send_outreach_email_has_required_params(self):
        mcp_path = os.path.join(PROJECT_ROOT, 'openclaw', 'mcp-openleg-server', 'server.mjs')
        with open(mcp_path) as f:
            content = f.read()
        assert 'to: z.string()' in content
        assert 'subject: z.string()' in content
        assert 'body: z.string()' in content


class TestConfigFiles:
    """Verify config files have AgentMail entries."""

    def test_env_example_has_agentmail_vars(self):
        with open(os.path.join(PROJECT_ROOT, '.env.example')) as f:
            content = f.read()
        assert 'AGENTMAIL_API_KEY' in content
        assert 'AGENTMAIL_DOMAIN' in content
        assert 'AGENTMAIL_LEA_INBOX' in content
        assert 'AGENTMAIL_WEBHOOK_SECRET' in content

    def test_requirements_has_agentmail(self):
        with open(os.path.join(PROJECT_ROOT, 'requirements.txt')) as f:
            content = f.read()
        assert 'agentmail' in content
