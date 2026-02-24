"""TDD tests for B2B API expansion - new enterprise endpoints."""
import pytest
from api_b2b import TIER_ACCESS


class TestTierAccess:
    """Test tier-based endpoint access control."""

    def test_starter_endpoints(self):
        assert "load_profiles" in TIER_ACCESS["starter"]
        assert "solar_index" in TIER_ACCESS["starter"]
        assert "formation_pipeline" not in TIER_ACCESS["starter"]

    def test_professional_endpoints(self):
        assert "formation_pipeline" in TIER_ACCESS["professional"]
        assert "community_benchmarks" in TIER_ACCESS["professional"]

    def test_enterprise_endpoints(self):
        assert "grid_optimization" in TIER_ACCESS["enterprise"]
        assert "formation_pipeline" in TIER_ACCESS["enterprise"]
        assert "community_benchmarks" in TIER_ACCESS["enterprise"]
        assert "raw_export" in TIER_ACCESS["enterprise"]


class TestFormationPipelineLogic:
    """Test formation pipeline data aggregation."""

    def test_pipeline_summary(self):
        from insights_engine import compute_formation_pipeline
        # Should return dict with stage counts
        result = compute_formation_pipeline(kanton="ZH")
        assert "stages" in result
        assert "total_communities" in result

    def test_pipeline_has_all_stages(self):
        from insights_engine import compute_formation_pipeline
        result = compute_formation_pipeline()
        stages = result["stages"]
        for s in ["interested", "formation_started", "dso_submitted", "dso_approved", "active"]:
            assert s in stages


class TestGridOptimizationLogic:
    """Test grid optimization insights."""

    def test_optimization_structure(self):
        from insights_engine import compute_grid_optimization
        result = compute_grid_optimization(kanton="ZH")
        assert "peak_reduction_potential_pct" in result
        assert "recommended_actions" in result
        assert isinstance(result["recommended_actions"], list)


class TestCommunityBenchmarks:
    """Test community benchmark aggregation."""

    def test_benchmark_structure(self):
        from insights_engine import compute_community_benchmarks
        result = compute_community_benchmarks(kanton="ZH")
        assert "avg_members" in result
        assert "avg_self_supply_ratio" in result
        assert "top_communities" in result
