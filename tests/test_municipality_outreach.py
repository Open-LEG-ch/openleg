"""Tests for municipality outreach generation in email_automation.py.

Covers US-004: Demand-aware municipality outreach pack generation.
"""
import os
import pytest
from unittest.mock import patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_HIGH_SIGNAL = {
    "signals": [{
        "bfs_number": 261,
        "name": "Dietikon",
        "kanton": "ZH",
        "demand_level": "high",
        "signal_type": "verified",
        "verified_demand": {
            "verified_buildings": 20,
            "recent_signups_90d": 10,
            "confirmed_leg_members": 5,
            "communities_in_formation": 2,
            "meter_data_uploads": 8,
            "demand_score": 75.0,
        },
        "heuristic_baseline": {"source": "public_data_only", "has_resident_data": True},
    }],
    "computed_at": "2026-03-29T00:00:00",
    "bfs_number": 261,
}

_EMPTY_SIGNAL = {
    "signals": [],
    "computed_at": "2026-03-29T00:00:00",
    "bfs_number": None,
}


# ---------------------------------------------------------------------------
# Template existence
# ---------------------------------------------------------------------------

class TestMunicipalityOutreachTemplate:
    def test_template_exists(self):
        path = os.path.join(PROJECT_ROOT, "templates", "emails", "municipality_outreach.html")
        assert os.path.exists(path)

    def test_template_has_demand_block(self):
        path = os.path.join(PROJECT_ROOT, "templates", "emails", "municipality_outreach.html")
        with open(path) as f:
            content = f.read()
        assert "has_demand_data" in content
        assert "demand_level" in content
        assert "municipality_name" in content
        assert "verified_buildings" in content

    def test_template_has_german_content(self):
        path = os.path.join(PROJECT_ROOT, "templates", "emails", "municipality_outreach.html")
        with open(path) as f:
            content = f.read()
        assert "Nachfrageübersicht" in content
        assert "Gemeinde" in content


# ---------------------------------------------------------------------------
# TRIGGER_TEMPLATES registration
# ---------------------------------------------------------------------------

class TestMunicipalityOutreachRegistration:
    def test_municipality_outreach_in_trigger_templates(self):
        import email_automation
        assert "municipality_outreach" in email_automation.TRIGGER_TEMPLATES
        config = email_automation.TRIGGER_TEMPLATES["municipality_outreach"]
        assert "subject" in config
        assert "template" in config

    def test_get_municipality_demand_context_callable(self):
        import email_automation
        assert callable(email_automation.get_municipality_demand_context)

    def test_render_municipality_outreach_callable(self):
        import email_automation
        assert callable(email_automation.render_municipality_outreach)


# ---------------------------------------------------------------------------
# get_municipality_demand_context
# ---------------------------------------------------------------------------

class TestGetMunicipalityDemandContext:
    def test_with_signal_returns_populated_context(self):
        import email_automation
        with patch("insights_engine.compute_municipality_demand_signal", return_value=_HIGH_SIGNAL):
            ctx = email_automation.get_municipality_demand_context(bfs_number=261)
        assert ctx["has_demand_data"] is True
        assert ctx["demand_level"] == "high"
        assert ctx["demand_score"] == 75.0
        assert ctx["verified_buildings"] == 20
        assert ctx["recent_signups_90d"] == 10
        assert ctx["confirmed_leg_members"] == 5
        assert ctx["communities_in_formation"] == 2

    def test_without_signal_returns_empty_context(self):
        import email_automation
        with patch("insights_engine.compute_municipality_demand_signal", return_value=_EMPTY_SIGNAL):
            ctx = email_automation.get_municipality_demand_context()
        assert ctx["has_demand_data"] is False
        assert ctx["demand_level"] == "none"
        assert ctx["demand_score"] == 0.0

    def test_on_exception_returns_empty_context(self):
        import email_automation
        with patch("insights_engine.compute_municipality_demand_signal", side_effect=Exception("db error")):
            ctx = email_automation.get_municipality_demand_context(bfs_number=261)
        assert ctx["has_demand_data"] is False
        assert ctx["demand_level"] == "none"
        assert ctx["demand_score"] == 0.0

    def test_context_keys_always_present(self):
        """All expected keys are present regardless of signal availability."""
        import email_automation
        required_keys = {
            "has_demand_data", "demand_level", "demand_score", "signal_type",
            "verified_buildings", "recent_signups_90d", "confirmed_leg_members",
            "communities_in_formation",
        }
        for signal in (_HIGH_SIGNAL, _EMPTY_SIGNAL):
            with patch("insights_engine.compute_municipality_demand_signal", return_value=signal):
                ctx = email_automation.get_municipality_demand_context()
            assert required_keys.issubset(ctx.keys()), f"Missing keys: {required_keys - ctx.keys()}"


# ---------------------------------------------------------------------------
# render_municipality_outreach
# ---------------------------------------------------------------------------

class TestRenderMunicipalityOutreach:
    def test_render_without_app_returns_dict_with_demand_context(self):
        """Without a Flask app fallback HTML is used, but dict structure is correct."""
        import email_automation
        with patch("insights_engine.compute_municipality_demand_signal", return_value=_HIGH_SIGNAL):
            result = email_automation.render_municipality_outreach(
                municipality_name="Dietikon",
                recipient_email="gemeinde@dietikon.ch",
                bfs_number=261,
            )
        assert "subject" in result
        assert "html_body" in result
        assert "demand_context" in result
        assert result["municipality_name"] == "Dietikon"
        assert result["demand_context"]["demand_level"] == "high"
        assert result["html_body"]

    def test_render_without_demand_data(self):
        """Render still succeeds when no demand signal is available."""
        import email_automation
        with patch("insights_engine.compute_municipality_demand_signal", return_value=_EMPTY_SIGNAL):
            result = email_automation.render_municipality_outreach(
                municipality_name="Urdorf",
                recipient_email="gemeinde@urdorf.ch",
            )
        assert result["demand_context"]["has_demand_data"] is False
        assert result["municipality_name"] == "Urdorf"
        assert result["html_body"]

    def test_subject_includes_municipality_name(self):
        """The rendered subject must contain the municipality name."""
        import email_automation
        with patch("insights_engine.compute_municipality_demand_signal", return_value=_EMPTY_SIGNAL):
            result = email_automation.render_municipality_outreach(
                municipality_name="Schlieren",
                recipient_email="info@schlieren.ch",
            )
        assert "Schlieren" in result["subject"]

    def test_render_returns_demand_context_with_signal(self):
        """When demand data is present the context is propagated into the result."""
        import email_automation
        with patch("insights_engine.compute_municipality_demand_signal", return_value=_HIGH_SIGNAL):
            result = email_automation.render_municipality_outreach(
                municipality_name="Dietikon",
                recipient_email="gemeinde@dietikon.ch",
                bfs_number=261,
            )
        ctx = result["demand_context"]
        assert ctx["verified_buildings"] == 20
        assert ctx["communities_in_formation"] == 2
