"""Tests for formation nudge email template and registration."""
import os
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestFormationNudgeTemplate:
    def test_formation_nudge_template_exists(self):
        path = os.path.join(PROJECT_ROOT, "templates", "emails", "formation_nudge.html")
        assert os.path.exists(path)

    def test_template_has_german_content(self):
        path = os.path.join(PROJECT_ROOT, "templates", "emails", "formation_nudge.html")
        with open(path) as f:
            content = f.read()
        assert "LEG-Gründung wartet" in content
        assert "community_name" in content
        assert "days_stuck" in content


class TestFormationNudgeRegistration:
    def test_formation_nudge_in_email_module(self):
        import email_automation
        assert "formation_nudge" in email_automation.TRIGGER_TEMPLATES
        config = email_automation.TRIGGER_TEMPLATES["formation_nudge"]
        assert "subject" in config
        assert "template" in config
