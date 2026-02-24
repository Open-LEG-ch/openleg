"""TDD tests for admin pipeline dashboard logic."""
import pytest
from sales_pipeline import get_pipeline_dashboard, get_pipeline, PIPELINE_STAGES


class TestPipelineDashboard:
    """Test pipeline dashboard aggregation."""

    def test_full_funnel(self):
        entries = [
            {"vnb_name": "EKZ", "status": "lead", "score": 80},
            {"vnb_name": "BKW", "status": "lead", "score": 60},
            {"vnb_name": "CKW", "status": "contacted", "score": 75},
            {"vnb_name": "IWB", "status": "demo", "score": 90},
            {"vnb_name": "ewz", "status": "paid", "score": 85},
        ]
        d = get_pipeline_dashboard(entries)
        assert d["total"] == 5
        assert d["funnel"]["lead"] == 2
        assert d["funnel"]["contacted"] == 1
        assert d["funnel"]["paid"] == 1
        assert d["conversion_rate"] == 20.0

    def test_empty_pipeline(self):
        d = get_pipeline_dashboard([])
        assert d["total"] == 0
        assert d["avg_score"] == 0

    def test_filter_by_status(self):
        entries = [
            {"vnb_name": "A", "status": "lead", "score": 50},
            {"vnb_name": "B", "status": "demo", "score": 70},
            {"vnb_name": "C", "status": "lead", "score": 90},
        ]
        leads = get_pipeline(entries, status_filter="lead")
        assert len(leads) == 2
        demos = get_pipeline(entries, status_filter="demo")
        assert len(demos) == 1

    def test_all_stages_present(self):
        d = get_pipeline_dashboard([])
        for stage in PIPELINE_STAGES:
            assert stage in d["funnel"]
