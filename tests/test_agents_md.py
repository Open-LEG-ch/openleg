# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for OpenClaw config and server.mjs tool count."""

import os
import json
import re
import subprocess
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestOpenClawConfig:
    @pytest.fixture(autouse=True)
    def load_config(self):
        path = os.path.join(PROJECT_ROOT, "openclaw", "config", "openclaw.example.json")
        with open(path) as f:
            self.config = json.load(f)

    def test_gateway_auth_mode_password(self):
        auth = self.config["gateway"]["auth"]
        assert auth["mode"] == "password"

    def test_config_uses_env_tokens(self):
        auth = self.config["gateway"]["auth"]
        assert auth["token"] == "${OPENCLAW_GATEWAY_TOKEN}"
        assert auth["password"] == "${OPENCLAW_GATEWAY_PASSWORD}"

    def test_cron_disabled(self):
        assert self.config["cron"]["enabled"] is False


class TestWorkspaceRepoBoundary:
    def test_workspace_docs_not_tracked(self):
        result = subprocess.run(
            ["git", "ls-files", "openclaw/config/workspace"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        tracked = [line for line in result.stdout.splitlines() if line.strip()]
        assert tracked == []


class TestServerMjs:
    @pytest.fixture(autouse=True)
    def load_server(self):
        path = os.path.join(
            PROJECT_ROOT, "openclaw", "mcp-openleg-server", "server.mjs"
        )
        with open(path) as f:
            self.content = f.read()

    def test_tool_count_at_least_44(self):
        count = len(re.findall(r"server\.tool\(", self.content))
        assert count >= 44, f"Expected >= 44 server.tool() calls, found {count}"

    @pytest.mark.parametrize(
        "tool_name",
        [
            "generate_leg_document",
            "list_documents",
            "run_billing_period",
            "get_billing_summary",
            "score_vnb",
            "draft_outreach",
            "get_unseeded_municipalities",
            "get_all_swiss_municipalities",
            "get_stuck_formations",
            "get_outreach_candidates",
        ],
    )
    def test_key_tools_exist(self, tool_name):
        assert re.search(rf"server\.tool\(\s*['\"]{tool_name}['\"]", self.content)
