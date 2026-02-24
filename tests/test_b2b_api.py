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
        result = compute_formation_pipeline(kanton="ZH")
        assert "stages" in result
        assert "total_communities" in result

    def test_pipeline_has_all_stages(self):
        from insights_engine import compute_formation_pipeline
        result = compute_formation_pipeline()
        stages = result["stages"]
        for s in ["interested", "formation_started", "dso_submitted", "dso_approved", "active"]:
            assert s in stages

    def test_avg_formation_days_with_data(self):
        """avg_formation_days computed from timestamps when available."""
        from unittest.mock import patch
        from datetime import datetime
        mock_communities = [
            {"status": "active", "formation_started_at": datetime(2026, 1, 1), "dso_approved_at": datetime(2026, 2, 15)},
            {"status": "active", "formation_started_at": datetime(2026, 1, 10), "dso_approved_at": datetime(2026, 3, 1)},
        ]
        with patch('insights_engine.db') as mock_db:
            mock_db.list_all_communities.return_value = mock_communities
            from insights_engine import compute_formation_pipeline
            result = compute_formation_pipeline()
            assert result["avg_formation_days"] > 0
            # (45 + 50) / 2 = 47.5
            assert result["avg_formation_days"] == 47.5


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

    def test_avg_self_supply_with_billing_data(self):
        """avg_self_supply_ratio computed from billing_line_items when available."""
        from unittest.mock import patch, MagicMock
        mock_communities = [
            {"community_id": "c1", "member_count": 5, "status": "active"},
        ]
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = {"avg_ratio": 0.42}
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch('insights_engine.db') as mock_db:
            mock_db.list_communities_by_kanton.return_value = mock_communities
            mock_db.get_connection.return_value = mock_conn
            from insights_engine import compute_community_benchmarks
            result = compute_community_benchmarks(kanton="ZH")
            assert result["avg_self_supply_ratio"] == 0.42
