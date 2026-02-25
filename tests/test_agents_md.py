"""Tests for OpenClaw config and server.mjs tool count."""
import os
import json
import re
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestOpenClawConfig:
    @pytest.fixture(autouse=True)
    def load_config(self):
        path = os.path.join(PROJECT_ROOT, "openclaw", "config", "openclaw.json")
        with open(path) as f:
            self.config = json.load(f)

    def test_gateway_auth_mode_password(self):
        auth = self.config["gateway"]["auth"]
        assert auth["mode"] == "password"

    def test_config_uses_env_tokens(self):
        auth = self.config["gateway"]["auth"]
        assert auth["token"] == "${OPENCLAW_GATEWAY_TOKEN}"
        assert auth["password"] == "${OPENCLAW_GATEWAY_PASSWORD}"


class TestServerMjs:
    @pytest.fixture(autouse=True)
    def load_server(self):
        path = os.path.join(PROJECT_ROOT, "openclaw", "mcp-badenleg-server", "server.mjs")
        with open(path) as f:
            self.content = f.read()

    def test_tool_count_at_least_40(self):
        count = len(re.findall(r'server\.tool\(', self.content))
        assert count >= 40, f"Expected >= 40 server.tool() calls, found {count}"

    @pytest.mark.parametrize("tool_name", [
        "generate_leg_document",
        "list_documents",
        "run_billing_period",
        "get_billing_summary",
        "score_vnb",
        "draft_outreach",
    ])
    def test_key_tools_exist(self, tool_name):
        assert re.search(rf"server\.tool\(\s*['\"]{tool_name}['\"]", self.content)
