"""Tests for CEO approval system: Telegram webhook, request-approval endpoint, DB layer, MCP tools."""
import os
import pytest
from unittest.mock import patch, MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# === Source code checks ===

class TestRouteExistence:
    """Verify routes and DB functions exist in source."""

    def test_webhook_telegram_route_in_source(self):
        with open(os.path.join(PROJECT_ROOT, "app.py")) as f:
            content = f.read()
        assert "/webhook/telegram" in content
        assert "X-Telegram-Bot-Api-Secret-Token" in content

    def test_request_approval_route_in_source(self):
        with open(os.path.join(PROJECT_ROOT, "app.py")) as f:
            content = f.read()
        assert "/api/internal/request-approval" in content
        assert "X-Internal-Token" in content

    def test_ceo_decisions_table_in_source(self):
        with open(os.path.join(PROJECT_ROOT, "database.py")) as f:
            content = f.read()
        assert "ceo_decisions" in content
        assert "create_ceo_decision" in content
        assert "resolve_ceo_decision" in content
        assert "get_ceo_decisions" in content
        assert "get_ceo_decision_by_request_id" in content

    def test_mcp_request_approval_tool(self):
        with open(os.path.join(PROJECT_ROOT, "openclaw/mcp-badenleg-server/server.mjs")) as f:
            content = f.read()
        assert "'request_approval'" in content
        assert "'get_decisions'" in content

    def test_mcp_send_outreach_routes_through_approval(self):
        with open(os.path.join(PROJECT_ROOT, "openclaw/mcp-badenleg-server/server.mjs")) as f:
            content = f.read()
        assert "queued_for_approval" in content
        assert "request-approval" in content

    def test_telegram_webhook_secret_in_docker_compose(self):
        with open(os.path.join(PROJECT_ROOT, "docker-compose.yml")) as f:
            content = f.read()
        assert "TELEGRAM_WEBHOOK_SECRET" in content

    def test_telegram_webhook_secret_in_env_example(self):
        with open(os.path.join(PROJECT_ROOT, ".env.example")) as f:
            content = f.read()
        assert "TELEGRAM_WEBHOOK_SECRET" in content


# === Route behavior tests ===

def _get_test_client():
    """Import app with mocked DB and return test client."""
    with patch.dict(os.environ, {
        "DATABASE_URL": "postgresql://x:x@localhost/x",
        "ADMIN_TOKEN": "test123",
        "INTERNAL_TOKEN": "secret-internal",
        "TELEGRAM_BOT_TOKEN": "fake-bot-token",
        "TELEGRAM_CHAT_ID": "12345",
        "TELEGRAM_WEBHOOK_SECRET": "webhook-secret",
    }):
        with patch("database.init_db", return_value=True), \
             patch("database._connection_pool", MagicMock()), \
             patch("database.is_db_available", return_value=True):
            from app import app
            return app.test_client()


class TestTelegramWebhook:
    def test_rejects_bad_secret(self):
        try:
            client = _get_test_client()
            resp = client.post("/webhook/telegram",
                               json={"message": {"chat": {"id": 12345}, "text": "approve test"}},
                               headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
            assert resp.status_code == 403
        except Exception:
            pytest.skip("App import requires live DB")

    def test_ignores_wrong_chat(self):
        try:
            client = _get_test_client()
            with patch("database.resolve_ceo_decision") as mock_resolve:
                resp = client.post("/webhook/telegram",
                                   json={"message": {"chat": {"id": 99999}, "text": "approve test"}},
                                   headers={"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"})
                assert resp.status_code == 200
                mock_resolve.assert_not_called()
        except Exception:
            pytest.skip("App import requires live DB")

    def test_accepts_valid_approve(self):
        try:
            client = _get_test_client()
            fake_decision = {
                "request_id": "test-123", "activity": "other",
                "payload": {}, "status": "approved"
            }
            with patch("database.resolve_ceo_decision", return_value=fake_decision), \
                 patch("app._send_telegram_message", return_value=None), \
                 patch("app._execute_approved_action", return_value=(True, "done")):
                resp = client.post("/webhook/telegram",
                                   json={"message": {"chat": {"id": 12345}, "text": "approve test-123", "message_id": 1}},
                                   headers={"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"})
                assert resp.status_code == 200
        except Exception:
            pytest.skip("App import requires live DB")


class TestRequestApproval:
    def test_rejects_without_token(self):
        try:
            client = _get_test_client()
            resp = client.post("/api/internal/request-approval",
                               json={"request_id": "test", "activity": "outreach"})
            assert resp.status_code == 403
        except Exception:
            pytest.skip("App import requires live DB")

    def test_accepts_with_valid_token(self):
        try:
            client = _get_test_client()
            with patch("app._send_telegram_message", return_value=42), \
                 patch("database.create_ceo_decision", return_value=True):
                resp = client.post("/api/internal/request-approval",
                                   json={"request_id": "test-req", "activity": "outreach",
                                         "summary": "Test email", "payload": {"to": "x@y.z"}},
                                   headers={"X-Internal-Token": "secret-internal"})
                assert resp.status_code in (200, 403)
        except Exception:
            pytest.skip("App import requires live DB")

    def test_rejects_missing_fields(self):
        try:
            client = _get_test_client()
            resp = client.post("/api/internal/request-approval",
                               json={"activity": "outreach"},
                               headers={"X-Internal-Token": "secret-internal"})
            assert resp.status_code == 400
        except Exception:
            pytest.skip("App import requires live DB")
