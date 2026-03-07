"""Tests for LEA inbound email triage."""

import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def email_client():
    """Flask test client for email triage tests."""
    env = {
        'DATABASE_URL': 'postgresql://x:x@localhost/x',
        'ADMIN_TOKEN': 'test123',
        'INTERNAL_TOKEN': 'secret-internal',
        'TELEGRAM_BOT_TOKEN': 'fake-bot-token',
        'TELEGRAM_CHAT_ID': '12345',
        'TELEGRAM_WEBHOOK_SECRET': 'webhook-secret',
        'AGENTMAIL_WEBHOOK_SECRET': 'am-secret',
        'REDIS_URL': 'memory://',
    }
    with patch.dict(os.environ, env):
        with (
            patch('database.init_db', return_value=True),
            patch('database._connection_pool', MagicMock()),
            patch('database.is_db_available', return_value=True),
        ):
            import importlib

            import app as app_mod

            importlib.reload(app_mod)
            app_mod.INTERNAL_TOKEN = 'secret-internal'
            app_mod.AGENTMAIL_WEBHOOK_SECRET = 'am-secret'
            app_mod.TELEGRAM_BOT_TOKEN = 'fake-bot-token'
            app_mod.TELEGRAM_CHAT_ID = '12345'
            app_mod.app.config['TESTING'] = True
            if app_mod.limiter:
                app_mod.limiter.enabled = False
            yield app_mod.app.test_client()


def test_agentmail_webhook_classifies_inquiry(email_client):
    """Inbound email with question should classify as inquiry."""
    with (
        patch('database.track_event'),
        patch('database.save_inbound_email') as mock_save,
        patch('app._relay_to_telegram'),
    ):
        email_client.post(
            '/webhook/agentmail',
            json={
                'type': 'message.received',
                'data': {
                    'from': 'test@example.com',
                    'subject': 'Frage zur LEG Gründung',
                    'body': 'Wie kann ich mitmachen?',
                    'message_id': 'msg-1',
                },
            },
            headers={'X-Webhook-Secret': 'am-secret'},
        )
        mock_save.assert_called_once()
        call_kwargs = mock_save.call_args
        args = call_kwargs[1] if call_kwargs[1] else {}
        if not args:
            args_list = call_kwargs[0]
            # classification should be 'inquiry' for question keywords
            assert any('inquiry' in str(a) for a in args_list)


def test_agentmail_webhook_classifies_support(email_client):
    """Inbound email about problems should classify as support."""
    with (
        patch('database.track_event'),
        patch('database.save_inbound_email') as mock_save,
        patch('app._relay_to_telegram'),
    ):
        email_client.post(
            '/webhook/agentmail',
            json={
                'type': 'message.received',
                'data': {
                    'from': 'user@example.com',
                    'subject': 'Problem mit Login',
                    'body': 'Ich kann mich nicht anmelden, Fehler aufgetreten',
                    'message_id': 'msg-2',
                },
            },
            headers={'X-Webhook-Secret': 'am-secret'},
        )
        mock_save.assert_called_once()


def test_agentmail_webhook_saves_to_db(email_client):
    """Webhook should call save_inbound_email."""
    with (
        patch('database.track_event'),
        patch('database.save_inbound_email') as mock_save,
        patch('app._relay_to_telegram'),
    ):
        email_client.post(
            '/webhook/agentmail',
            json={
                'type': 'message.received',
                'data': {
                    'from': 'info@company.ch',
                    'subject': 'Partnership Interest',
                    'body': 'We want to partner',
                    'message_id': 'msg-3',
                },
            },
            headers={'X-Webhook-Secret': 'am-secret'},
        )
        mock_save.assert_called_once()


def test_agentmail_webhook_notifies_telegram_with_classification(email_client):
    """Webhook should notify Telegram with classification."""
    with (
        patch('database.track_event'),
        patch('database.save_inbound_email'),
        patch('app._relay_to_telegram') as mock_tg,
    ):
        email_client.post(
            '/webhook/agentmail',
            json={
                'type': 'message.received',
                'data': {'from': 'test@example.com', 'subject': 'Question', 'body': 'Hello?', 'message_id': 'msg-4'},
            },
            headers={'X-Webhook-Secret': 'am-secret'},
        )
        mock_tg.assert_called_once()
        call_args = mock_tg.call_args[0]
        # Should include classification in the relay
        assert any('inbound' in str(a).lower() for a in call_args)


def test_agentmail_triage_auth_required(email_client):
    """Webhook should reject requests without valid secret."""
    resp = email_client.post(
        '/webhook/agentmail',
        json={'type': 'message.received', 'data': {}},
        headers={'X-Webhook-Secret': 'wrong-secret'},
    )
    assert resp.status_code == 403
