"""Tests for LEA strategy prioritization + status triggers."""
import os
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def strat_client():
    """Flask test client for strategy tests."""
    env = {
        "DATABASE_URL": "postgresql://x:x@localhost/x",
        "ADMIN_TOKEN": "test123",
        "INTERNAL_TOKEN": "secret-internal",
        "TELEGRAM_BOT_TOKEN": "fake-bot-token",
        "TELEGRAM_CHAT_ID": "12345",
        "TELEGRAM_WEBHOOK_SECRET": "webhook-secret",
        "REDIS_URL": "memory://",
    }
    with patch.dict(os.environ, env):
        with patch("database.init_db", return_value=True), \
             patch("database._connection_pool", MagicMock()), \
             patch("database.is_db_available", return_value=True):
            import importlib
            import event_hooks
            event_hooks.clear()
            import app as app_mod
            importlib.reload(app_mod)
            app_mod.INTERNAL_TOKEN = "secret-internal"
            app_mod.TELEGRAM_BOT_TOKEN = "fake-bot-token"
            app_mod.TELEGRAM_CHAT_ID = "12345"
            app_mod.app.config['TESTING'] = True
            if app_mod.limiter:
                app_mod.limiter.enabled = False
            yield app_mod.app.test_client()


def test_prioritization_tool_exists():
    """server.mjs should have get_strategy_prioritized_outreach tool."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "openclaw", "mcp-badenleg-server", "server.mjs"
    )
    with open(path) as f:
        content = f.read()
    assert 'get_strategy_prioritized_outreach' in content


def test_prioritization_uses_strategy_status():
    """Prioritization tool should JOIN strategy_tracker."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "openclaw", "mcp-badenleg-server", "server.mjs"
    )
    with open(path) as f:
        content = f.read()
    idx = content.index("'get_strategy_prioritized_outreach'")
    next_tool = content.find("server.tool(", idx + 1)
    handler = content[idx:next_tool if next_tool > 0 else len(content)]
    assert 'strategy_tracker' in handler or 'vnb_pipeline' in handler


def test_prioritization_falls_back_to_score():
    """Prioritization should order by score when no strategy data."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "openclaw", "mcp-badenleg-server", "server.mjs"
    )
    with open(path) as f:
        content = f.read()
    idx = content.index("'get_strategy_prioritized_outreach'")
    next_tool = content.find("server.tool(", idx + 1)
    handler = content[idx:next_tool if next_tool > 0 else len(content)]
    assert 'score' in handler.lower() or 'ORDER' in handler


def test_strategy_item_change_fires_event(strat_client):
    """strategy_status_changed event should fire via notify-event."""
    import event_hooks
    event_hooks.clear()
    fired = []
    event_hooks.register('strategy_status_changed', lambda p: fired.append(p))
    with patch("database.track_event"), \
         patch("app._send_telegram_message"):
        resp = strat_client.post("/api/internal/notify-event",
                                  json={"event_type": "strategy_status_changed",
                                        "payload": {"item": "seed-municipalities", "status": "done"}},
                                  headers={"X-Internal-Token": "secret-internal"})
        assert resp.status_code == 200
        assert len(fired) == 1


def test_strategy_done_notifies_telegram(strat_client):
    """strategy_status_changed with status=done should reach Telegram."""
    with patch("database.track_event"), \
         patch("app._send_telegram_message") as mock_tg:
        strat_client.post("/api/internal/notify-event",
                           json={"event_type": "strategy_status_changed",
                                 "payload": {"item": "test-item", "status": "done"}},
                           headers={"X-Internal-Token": "secret-internal"})
        mock_tg.assert_called_once()


def test_strategy_blocked_alerts_ceo(strat_client):
    """strategy_status_changed with status=blocked should alert CEO."""
    with patch("database.track_event"), \
         patch("app._send_telegram_message") as mock_tg:
        strat_client.post("/api/internal/notify-event",
                           json={"event_type": "strategy_status_changed",
                                 "payload": {"item": "blocked-item", "status": "blocked"}},
                           headers={"X-Internal-Token": "secret-internal"})
        mock_tg.assert_called_once()


def test_strategy_needs_ceo_creates_approval(strat_client):
    """server.mjs track_strategy_item should POST to notify-event on status change."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "openclaw", "mcp-badenleg-server", "server.mjs"
    )
    with open(path) as f:
        content = f.read()
    idx = content.index("'track_strategy_item'")
    next_tool = content.find("server.tool(", idx + 1)
    handler = content[idx:next_tool if next_tool > 0 else len(content)]
    assert 'notify-event' in handler or 'strategy_status_changed' in handler
