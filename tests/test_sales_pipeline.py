"""TDD tests for sales_pipeline.py - VNB sales pipeline management."""
import pytest
from unittest.mock import patch, MagicMock
from sales_pipeline import (
    score_vnb,
    get_pipeline,
    update_pipeline_status,
    draft_outreach_email,
    get_pipeline_dashboard,
    PIPELINE_STAGES,
)


class TestPipelineStages:
    """Verify pipeline stage definitions."""

    def test_stage_order(self):
        assert PIPELINE_STAGES == ["lead", "contacted", "demo", "trial", "paid", "churned"]

    def test_valid_transitions(self):
        from sales_pipeline import is_valid_transition
        assert is_valid_transition("lead", "contacted") is True
        assert is_valid_transition("contacted", "demo") is True
        assert is_valid_transition("demo", "lead") is False  # can't go backwards
        assert is_valid_transition("paid", "churned") is True


class TestScoring:
    """Test VNB auto-scoring."""

    def test_score_high_potential(self):
        score = score_vnb(
            population=50000,
            solar_potential_kwh=1200,
            has_leghub=False,
            smart_meter_coverage=0.6,
        )
        assert 70 <= score <= 100

    def test_score_low_potential(self):
        score = score_vnb(
            population=1000,
            solar_potential_kwh=800,
            has_leghub=True,
            smart_meter_coverage=0.2,
        )
        assert score < 50

    def test_score_bounds(self):
        # Extreme values should stay 0-100
        score = score_vnb(population=0, solar_potential_kwh=0, has_leghub=True, smart_meter_coverage=0)
        assert 0 <= score <= 100
        score = score_vnb(population=500000, solar_potential_kwh=2000, has_leghub=False, smart_meter_coverage=1.0)
        assert 0 <= score <= 100


class TestOutreachDraft:
    """Test outreach email draft generation."""

    def test_draft_contains_vnb_name(self):
        draft = draft_outreach_email(
            vnb_name="Stadtwerk Winterthur",
            population=115000,
            value_gap_chf=180,
            solar_potential_kwh=1050,
        )
        assert "Winterthur" in draft
        assert "LEG" in draft or "Elektrizitätsgemeinschaft" in draft

    def test_draft_is_german(self):
        draft = draft_outreach_email(
            vnb_name="EW Buchs",
            population=5000,
            value_gap_chf=120,
            solar_potential_kwh=950,
        )
        # Should contain German text
        assert any(w in draft for w in ["Gemeinde", "Strom", "Energie", "LEG"])


class TestPipelineDashboard:
    """Test funnel metrics."""

    def test_dashboard_structure(self):
        entries = [
            {"status": "lead", "score": 80},
            {"status": "lead", "score": 60},
            {"status": "contacted", "score": 75},
            {"status": "demo", "score": 90},
            {"status": "paid", "score": 85},
        ]
        dashboard = get_pipeline_dashboard(entries)
        assert dashboard["total"] == 5
        assert dashboard["funnel"]["lead"] == 2
        assert dashboard["funnel"]["contacted"] == 1
        assert dashboard["funnel"]["demo"] == 1
        assert dashboard["funnel"]["paid"] == 1
        assert dashboard["funnel"]["trial"] == 0
        assert "avg_score" in dashboard
