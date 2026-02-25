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

    def test_cron_enabled(self):
        assert self.config["cron"]["enabled"] is True


class TestCronJobs:
    @pytest.fixture(autouse=True)
    def load_jobs(self):
        path = os.path.join(PROJECT_ROOT, "openclaw", "config", "cron", "jobs.json")
        with open(path) as f:
            self.jobs = json.load(f)

    def test_jobs_file_exists(self):
        path = os.path.join(PROJECT_ROOT, "openclaw", "config", "cron", "jobs.json")
        assert os.path.exists(path)

    def test_jobs_has_four_entries(self):
        assert len(self.jobs) == 4

    def test_each_job_has_required_fields(self):
        required = {"name", "schedule", "sessionTarget", "payload"}
        for job in self.jobs:
            assert required.issubset(job.keys()), f"Job {job.get('name')} missing fields"

    def test_schedules_are_valid_cron(self):
        cron_re = re.compile(r'^(\S+\s+){4}\S+$')
        for job in self.jobs:
            expr = job["schedule"]["expr"]
            assert cron_re.match(expr), f"Invalid cron: {expr}"

    def test_jobs_have_webhook_delivery(self):
        for job in self.jobs:
            assert job["delivery"]["mode"] == "webhook"
            assert "lea-report" in job["delivery"]["to"]


class TestAgentsMd:
    @pytest.fixture(autouse=True)
    def load_agents(self):
        path = os.path.join(PROJECT_ROOT, "openclaw", "config", "workspace", "AGENTS.md")
        with open(path) as f:
            self.content = f.read()

    def test_agents_md_has_cron_section(self):
        assert "Autonomous Schedules" in self.content or ("Cron" in self.content and "daily" in self.content)

    def test_agents_md_has_seeding_tools(self):
        assert "get_unseeded_municipalities" in self.content
        assert "get_all_swiss_municipalities" in self.content

    def test_agents_md_has_stuck_formations_tool(self):
        assert "get_stuck_formations" in self.content

    def test_agents_md_has_outreach_candidates_tool(self):
        assert "get_outreach_candidates" in self.content


class TestServerMjs:
    @pytest.fixture(autouse=True)
    def load_server(self):
        path = os.path.join(PROJECT_ROOT, "openclaw", "mcp-badenleg-server", "server.mjs")
        with open(path) as f:
            self.content = f.read()

    def test_tool_count_at_least_44(self):
        count = len(re.findall(r'server\.tool\(', self.content))
        assert count >= 44, f"Expected >= 44 server.tool() calls, found {count}"

    @pytest.mark.parametrize("tool_name", [
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
    ])
    def test_key_tools_exist(self, tool_name):
        assert re.search(rf"server\.tool\(\s*['\"]{tool_name}['\"]", self.content)
