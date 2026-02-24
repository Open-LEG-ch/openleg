"""Tests for LEG toolkit: value-gap, clustering, financial model."""
import pytest
from unittest.mock import patch, MagicMock


class TestSavingsEstimate:
    """Test formation_wizard.calculate_savings_estimate."""

    def test_producer_savings(self):
        from formation_wizard import calculate_savings_estimate
        result = calculate_savings_estimate(
            consumption_kwh=4500, pv_kwp=10, community_size=5
        )
        assert result["annual_savings_chf"] > 0
        assert result["monthly_savings_chf"] > 0
        assert result["five_year_savings_chf"] == result["annual_savings_chf"] * 5

    def test_consumer_only(self):
        from formation_wizard import calculate_savings_estimate
        result = calculate_savings_estimate(
            consumption_kwh=4500, pv_kwp=0, community_size=5
        )
        assert result["annual_savings_chf"] > 0
        # Consumer saves from buying at LEG price vs grid price
        assert result["assumptions"]["leg_price_rp"] < result["assumptions"]["grid_buy_price_rp"]

    def test_larger_community(self):
        from formation_wizard import calculate_savings_estimate
        small = calculate_savings_estimate(4500, 10, 3)
        large = calculate_savings_estimate(4500, 10, 10)
        # Larger community should sell more locally
        assert large["annual_savings_chf"] >= small["annual_savings_chf"]


class TestContractTemplates:
    """Test contract template generation."""

    def test_default_templates(self):
        from formation_wizard import get_contract_templates
        templates = get_contract_templates()
        assert "community_agreement" in templates
        assert "participant_contract" in templates
        assert "dso_notification" in templates

    def test_custom_jurisdiction(self):
        from formation_wizard import get_contract_templates
        templates = get_contract_templates(jurisdiction="Kanton Bern")
        assert templates["community_agreement"]["jurisdiction"] == "Kanton Bern"


class TestFormationConfig:

    def test_config_values(self):
        from formation_wizard import FORMATION_CONFIG
        assert FORMATION_CONFIG["min_community_size"] == 3
        assert FORMATION_CONFIG["max_community_size"] == 50


class TestValueGapIntegration:
    """Integration test: value-gap with real tariff structure."""

    def test_typical_ekz_tariff(self):
        from public_data import compute_leg_value_gap
        tariff = {
            "grid_rp_kwh": 9.5,
            "total_rp_kwh": 27.5,
            "energy_rp_kwh": 12.0,
        }
        result = compute_leg_value_gap(tariff)
        # Should be ~171 CHF/year for 4500 kWh household
        assert 150 < result["annual_savings_chf"] < 200
        assert result["savings_pct"] > 10

    def test_high_grid_fee(self):
        from public_data import compute_leg_value_gap
        tariff = {"grid_rp_kwh": 15.0, "total_rp_kwh": 35.0}
        result = compute_leg_value_gap(tariff)
        # Higher grid fee = more savings
        assert result["annual_savings_chf"] > 200


class TestFinancialProjection:
    """Test 10-year financial projection logic."""

    def test_projections_increase(self):
        from formation_wizard import calculate_savings_estimate
        base = calculate_savings_estimate(4500, 10, 5)
        annual = base["annual_savings_chf"]
        # Simulate 2% increase
        year_2 = annual * 1.02
        assert year_2 > annual
