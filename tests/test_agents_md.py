"""Tests for AGENTS.md completeness and server.mjs tool count."""
import os
import re
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestAgentsMD:
    @pytest.fixture(autouse=True)
    def load_agents(self):
        path = os.path.join(PROJECT_ROOT, "openclaw", "config", "workspace", "AGENTS.md")
        with open(path) as f:
            self.content = f.read()

    @pytest.mark.parametrize("tool", [
        "generate_leg_document",
        "list_documents",
        "run_billing_period",
        "get_billing_summary",
        "score_vnb",
        "draft_outreach",
    ])
    def test_tool_documented(self, tool):
        assert f"`{tool}`" in self.content

    def test_has_document_billing_section(self):
        assert "Document & Billing Tools" in self.content


class TestServerMjs:
    @pytest.fixture(autouse=True)
    def load_server(self):
        path = os.path.join(PROJECT_ROOT, "openclaw", "mcp-badenleg-server", "server.mjs")
        with open(path) as f:
            self.content = f.read()

    def test_tool_count_at_least_40(self):
        count = len(re.findall(r'server\.tool\(', self.content))
        assert count >= 40, f"Expected >= 40 server.tool() calls, found {count}"
