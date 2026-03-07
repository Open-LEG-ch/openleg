"""Tests for LEA guardrails: tiered actions, budgets, circuit breaker."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def guard_client():
    """Flask test client with mocked DB and env vars for guardrail tests."""
    env = {
        'DATABASE_URL': 'postgresql://x:x@localhost/x',
        'ADMIN_TOKEN': 'test123',
        'INTERNAL_TOKEN': 'secret-internal',
        'TELEGRAM_BOT_TOKEN': 'fake-bot-token',
        'TELEGRAM_CHAT_ID': '12345',
        'TELEGRAM_WEBHOOK_SECRET': 'webhook-secret',
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
            app_mod.TELEGRAM_BOT_TOKEN = 'fake-bot-token'
            app_mod.TELEGRAM_CHAT_ID = '12345'
            app_mod.TELEGRAM_WEBHOOK_SECRET = 'webhook-secret'
            app_mod.app.config['TESTING'] = True
            if app_mod.limiter:
                app_mod.limiter.enabled = False
            yield app_mod.app.test_client()


# === check-budget: allowed when under limit ===


def test_check_budget_allowed_under_limit(guard_client):
    with (
        patch('database.get_lea_circuit_breaker', return_value=False),
        patch('database.count_budget_events', return_value=5),
    ):
        resp = guard_client.post(
            '/api/internal/check-budget',
            json={'event_type': 'lea_send_outreach_email', 'limit': 20, 'window': 86400},
            headers={'X-Internal-Token': 'secret-internal'},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['allowed'] is True
        assert data['used'] == 5
        assert data['limit'] == 20


# === check-budget: denied when at limit ===


def test_check_budget_denied_at_limit(guard_client):
    with (
        patch('database.get_lea_circuit_breaker', return_value=False),
        patch('database.count_budget_events', return_value=20),
    ):
        resp = guard_client.post(
            '/api/internal/check-budget',
            json={'event_type': 'lea_send_outreach_email', 'limit': 20, 'window': 86400},
            headers={'X-Internal-Token': 'secret-internal'},
        )
        data = resp.get_json()
        assert data['allowed'] is False
        assert data['used'] == 20


# === check-budget: denied when circuit breaker tripped ===


def test_check_budget_denied_circuit_breaker(guard_client):
    with patch('database.get_lea_circuit_breaker', return_value=True):
        resp = guard_client.post(
            '/api/internal/check-budget',
            json={'event_type': 'lea_send_outreach_email', 'limit': 20, 'window': 86400},
            headers={'X-Internal-Token': 'secret-internal'},
        )
        data = resp.get_json()
        assert data['allowed'] is False
        assert data['reason'] == 'circuit_breaker_tripped'


# === check-budget: auth required ===


def test_check_budget_rejects_without_token(guard_client):
    resp = guard_client.post('/api/internal/check-budget', json={'event_type': 'test', 'limit': 10, 'window': 86400})
    assert resp.status_code == 403


# === check-budget: no limit returns allowed ===


def test_check_budget_no_limit_returns_allowed(guard_client):
    with patch('database.get_lea_circuit_breaker', return_value=False):
        resp = guard_client.post(
            '/api/internal/check-budget',
            json={'event_type': 'some_event'},
            headers={'X-Internal-Token': 'secret-internal'},
        )
        data = resp.get_json()
        assert data['allowed'] is True


# === notify-yellow: sends Telegram and logs event ===


def test_notify_yellow_sends_telegram(guard_client):
    with patch('app._send_telegram_message', return_value=42) as mock_tg, patch('database.track_event') as mock_track:
        resp = guard_client.post(
            '/api/internal/notify-yellow',
            json={'tool_name': 'upsert_tenant', 'summary': 'Created tenant test'},
            headers={'X-Internal-Token': 'secret-internal'},
        )
        assert resp.status_code == 200
        mock_tg.assert_called_once()
        call_text = mock_tg.call_args[0][0]
        assert 'YELLOW ACTION' in call_text
        assert 'upsert' in call_text and 'tenant' in call_text
        mock_track.assert_called_once()
        assert 'lea_yellow_upsert_tenant' in mock_track.call_args[0][0]


# === notify-yellow: auth required ===


def test_notify_yellow_rejects_without_token(guard_client):
    resp = guard_client.post('/api/internal/notify-yellow', json={'tool_name': 'test', 'summary': 'test'})
    assert resp.status_code == 403


# === trigger_email approval flow ===


def test_trigger_email_approval_flow(guard_client):
    """Approve trigger_email: should insert scheduled_emails row."""
    import app as app_mod

    fake_building = {'email': 'test@example.com', 'building_id': 'b-123'}
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = {'id': 42}
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__ = lambda s: fake_cursor
    fake_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    decision = {
        'request_id': 'trigger-test',
        'activity': 'trigger_email',
        'payload': {'building_id': 'b-123', 'template_key': 'welcome', 'send_at': '2026-01-01T00:00:00'},
    }
    with (
        patch('database.get_building', return_value=fake_building),
        patch('database.get_connection') as mock_conn,
        patch('database.track_event'),
    ):
        mock_conn.return_value.__enter__ = lambda s: fake_conn
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        success, detail = app_mod._execute_approved_action(decision)
        assert success
        assert 'Scheduled email' in detail
        fake_cursor.execute.assert_called_once()
        sql = fake_cursor.execute.call_args[0][0]
        assert 'scheduled_emails' in sql


# === update_consent approval flow ===


def test_update_consent_approval_flow(guard_client):
    """Approve update_consent: should update consents table."""
    import app as app_mod

    fake_cursor = MagicMock()
    fake_cursor.rowcount = 1
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__ = lambda s: fake_cursor
    fake_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    decision = {
        'request_id': 'consent-test',
        'activity': 'update_consent',
        'payload': {'building_id': 'b-123', 'share_with_neighbors': True},
    }
    with patch('database.get_connection') as mock_conn, patch('database.track_event'):
        mock_conn.return_value.__enter__ = lambda s: fake_conn
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        success, detail = app_mod._execute_approved_action(decision)
        assert success
        assert 'Updated consent' in detail
        sql = fake_cursor.execute.call_args[0][0]
        assert 'consents' in sql


# === circuit breaker trips after 3 denials ===


def test_circuit_breaker_trips_after_3_denials(guard_client):
    fake_decision = {'request_id': 'deny-test', 'activity': 'outreach', 'payload': {}, 'status': 'denied'}
    with (
        patch('database.resolve_ceo_decision', return_value=fake_decision),
        patch('app._send_telegram_message', return_value=None) as mock_tg,
        patch('database.count_recent_denials', return_value=3),
        patch('database.set_lea_circuit_breaker') as mock_cb,
    ):
        resp = guard_client.post(
            '/webhook/telegram',
            json={'message': {'chat': {'id': 12345}, 'text': 'deny deny-test', 'message_id': 1}},
            headers={'X-Telegram-Bot-Api-Secret-Token': 'webhook-secret'},
        )
        assert resp.status_code == 200
        mock_cb.assert_called_once_with(True)
        call_text = mock_tg.call_args[0][0]
        assert 'Circuit breaker tripped' in call_text


# === circuit breaker does NOT trip with < 3 denials ===


def test_circuit_breaker_not_tripped_under_threshold(guard_client):
    fake_decision = {'request_id': 'deny-ok', 'activity': 'outreach', 'payload': {}, 'status': 'denied'}
    with (
        patch('database.resolve_ceo_decision', return_value=fake_decision),
        patch('app._send_telegram_message', return_value=None),
        patch('database.count_recent_denials', return_value=2),
        patch('database.set_lea_circuit_breaker') as mock_cb,
    ):
        resp = guard_client.post(
            '/webhook/telegram',
            json={'message': {'chat': {'id': 12345}, 'text': 'deny deny-ok', 'message_id': 1}},
            headers={'X-Telegram-Bot-Api-Secret-Token': 'webhook-secret'},
        )
        assert resp.status_code == 200
        mock_cb.assert_not_called()


# === reset-lea resets circuit breaker ===


def test_reset_lea_command(guard_client):
    with (
        patch('database.set_lea_circuit_breaker') as mock_cb,
        patch('app._send_telegram_message', return_value=None) as mock_tg,
    ):
        resp = guard_client.post(
            '/webhook/telegram',
            json={'message': {'chat': {'id': 12345}, 'text': 'reset-lea', 'message_id': 1}},
            headers={'X-Telegram-Bot-Api-Secret-Token': 'webhook-secret'},
        )
        assert resp.status_code == 200
        mock_cb.assert_called_once_with(False)
        call_text = mock_tg.call_args[0][0]
        assert 'circuit breaker reset' in call_text.lower()


# === budget counting uses correct time window ===


def test_check_budget_passes_window_correctly(guard_client):
    with (
        patch('database.get_lea_circuit_breaker', return_value=False),
        patch('database.count_budget_events', return_value=0) as mock_count,
    ):
        guard_client.post(
            '/api/internal/check-budget',
            json={'event_type': 'lea_send_telegram', 'limit': 30, 'window': 3600},
            headers={'X-Internal-Token': 'secret-internal'},
        )
        mock_count.assert_called_once_with('lea_send_telegram', 3600)


# === help text includes reset-lea ===


def test_help_text_includes_reset_lea(guard_client):
    with patch('app._send_telegram_message', return_value=None) as mock_tg:
        resp = guard_client.post(
            '/webhook/telegram',
            json={'message': {'chat': {'id': 12345}, 'text': 'hello', 'message_id': 1}},
            headers={'X-Telegram-Bot-Api-Secret-Token': 'webhook-secret'},
        )
        assert resp.status_code == 200
        call_text = mock_tg.call_args[0][0]
        assert 'reset-lea' in call_text


# === server.mjs has ACTION_REGISTRY ===


def test_server_has_action_registry():
    server_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'openclaw', 'mcp-openleg-server', 'server.mjs'
    )
    with open(server_path) as f:
        content = f.read()
    assert 'ACTION_REGISTRY' in content
    assert 'tierGuard' in content
    assert 'checkBudget' in content
    assert 'notifyYellow' in content


# === server.mjs: RED tools go through request-approval ===


def test_red_tools_use_request_approval():
    server_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'openclaw', 'mcp-openleg-server', 'server.mjs'
    )
    with open(server_path) as f:
        content = f.read()
    # trigger_email, update_consent, generate_leg_document should all POST to request-approval
    for tool in ['trigger_email', 'update_consent', 'generate_leg_document']:
        # Find the tool definition and check it references request-approval
        idx = content.index(f"'{tool}'")
        # Look in the next ~1500 chars for the request-approval call
        chunk = content[idx : idx + 1500]
        assert 'request-approval' in chunk, f'{tool} should route through request-approval'


# === DB functions exist ===


def test_db_guardrail_functions_exist():
    import database as db_mod

    assert hasattr(db_mod, 'count_budget_events')
    assert hasattr(db_mod, 'count_recent_denials')
    assert hasattr(db_mod, 'set_lea_circuit_breaker')
    assert hasattr(db_mod, 'get_lea_circuit_breaker')


# === platform_settings table in init ===


def test_platform_settings_table_in_init():
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database.py')
    with open(db_path) as f:
        content = f.read()
    assert 'platform_settings' in content
    assert 'lea_circuit_breaker' in content


# === generate_leg_document approval flow ===


def test_generate_leg_document_approval_flow(guard_client):
    import app as app_mod

    decision = {
        'request_id': 'legdoc-test',
        'activity': 'generate_leg_document',
        'payload': {'community_id': 'com-123', 'doc_type': 'reglement'},
    }
    with patch('urllib.request.urlopen') as mock_urlopen, patch('database.track_event'):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({'ok': True}).encode()
        mock_urlopen.return_value = mock_resp
        success, detail = app_mod._execute_approved_action(decision)
        assert success
        assert 'reglement' in detail


# === count_budget_events fail-closed ===


def test_count_budget_events_fail_closed():
    """On DB error, count_budget_events should return 999 (fail-closed)."""
    import database as db_mod

    with patch('database.get_connection', side_effect=Exception('DB down')):
        result = db_mod.count_budget_events('test', 86400)
        assert result == 999


# === get_lea_circuit_breaker fail-closed ===


def test_get_circuit_breaker_fail_closed():
    """On DB error, get_lea_circuit_breaker should return True (fail-closed)."""
    import database as db_mod

    with patch('database.get_connection', side_effect=Exception('DB down')):
        result = db_mod.get_lea_circuit_breaker()
        assert result is True


# === Promoted tools: YELLOW -> GREEN ===


def test_track_strategy_item_is_green_tier():
    server_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'openclaw', 'mcp-openleg-server', 'server.mjs'
    )
    with open(server_path) as f:
        content = f.read()
    idx = content.index('track_strategy_item')
    chunk = content[idx : idx + 80]
    assert 'GREEN' in chunk, 'track_strategy_item should be GREEN tier'


def test_update_vnb_status_is_green_tier():
    server_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'openclaw', 'mcp-openleg-server', 'server.mjs'
    )
    with open(server_path) as f:
        content = f.read()
    idx = content.index('update_vnb_status')
    chunk = content[idx : idx + 80]
    assert 'GREEN' in chunk, 'update_vnb_status should be GREEN tier'


def test_add_vnb_lead_is_green_tier():
    server_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'openclaw', 'mcp-openleg-server', 'server.mjs'
    )
    with open(server_path) as f:
        content = f.read()
    idx = content.index('add_vnb_lead')
    chunk = content[idx : idx + 80]
    assert 'GREEN' in chunk, 'add_vnb_lead should be GREEN tier'


def test_green_tools_still_respect_readonly():
    """Promoted GREEN tools should still have readonlyGuard in their handler."""
    server_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'openclaw', 'mcp-openleg-server', 'server.mjs'
    )
    with open(server_path) as f:
        content = f.read()
    for tool in ['track_strategy_item', 'update_vnb_status', 'add_vnb_lead']:
        idx = content.index(f"'{tool}'")
        chunk = content[idx : idx + 800]
        assert 'readonlyGuard' in chunk, f'{tool} should still use readonlyGuard'


def test_green_tools_do_not_call_notify_yellow():
    """Promoted GREEN tools should NOT call notifyYellow."""
    server_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'openclaw', 'mcp-openleg-server', 'server.mjs'
    )
    with open(server_path) as f:
        content = f.read()
    for tool in ['track_strategy_item', 'update_vnb_status', 'add_vnb_lead']:
        idx = content.index(f"'{tool}'")
        # Find the end of this tool handler (next server.tool call or end)
        next_tool = content.find('server.tool(', idx + 1)
        if next_tool == -1:
            next_tool = len(content)
        handler_chunk = content[idx:next_tool]
        assert 'notifyYellow' not in handler_chunk, f'{tool} should not call notifyYellow after promotion to GREEN'
