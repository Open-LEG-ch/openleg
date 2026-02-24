"""TDD tests for billing_engine.py - 15-min interval energy allocation."""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime


class TestProportionalAllocation:
    """Test proportional distribution of solar production."""

    def test_basic_proportional(self):
        from billing_engine import allocate_energy
        # 2 consumers, 1 producer, single 15-min interval
        production = pd.Series([10.0])  # 10 kWh produced
        consumption = pd.DataFrame({
            "consumer_a": [6.0],
            "consumer_b": [4.0],
        })
        result = allocate_energy(production, consumption, model="proportional")
        # consumer_a gets 60% of 10 = 6, consumer_b gets 40% of 10 = 4
        assert abs(result["consumer_a"].iloc[0] - 6.0) < 0.01
        assert abs(result["consumer_b"].iloc[0] - 4.0) < 0.01

    def test_production_exceeds_consumption(self):
        from billing_engine import allocate_energy
        production = pd.Series([20.0])
        consumption = pd.DataFrame({
            "consumer_a": [6.0],
            "consumer_b": [4.0],
        })
        result = allocate_energy(production, consumption, model="proportional")
        # Can't allocate more than consumed: a=6, b=4
        assert abs(result["consumer_a"].iloc[0] - 6.0) < 0.01
        assert abs(result["consumer_b"].iloc[0] - 4.0) < 0.01

    def test_production_less_than_consumption(self):
        from billing_engine import allocate_energy
        production = pd.Series([5.0])
        consumption = pd.DataFrame({
            "consumer_a": [6.0],
            "consumer_b": [4.0],
        })
        result = allocate_energy(production, consumption, model="proportional")
        # 5 kWh split proportionally: a=3, b=2
        assert abs(result["consumer_a"].iloc[0] - 3.0) < 0.01
        assert abs(result["consumer_b"].iloc[0] - 2.0) < 0.01

    def test_multiple_intervals(self):
        from billing_engine import allocate_energy
        production = pd.Series([10.0, 5.0, 0.0])
        consumption = pd.DataFrame({
            "a": [4.0, 3.0, 2.0],
            "b": [6.0, 2.0, 3.0],
        })
        result = allocate_energy(production, consumption, model="proportional")
        assert len(result) == 3
        # interval 0: production=10, total_consumption=10, full coverage
        assert abs(result["a"].iloc[0] - 4.0) < 0.01
        # interval 1: production=5, total=5, full coverage
        assert abs(result["a"].iloc[1] - 3.0) < 0.01
        # interval 2: production=0, no allocation
        assert abs(result["a"].iloc[2] - 0.0) < 0.01


class TestEqualAllocation:
    """Test equal (einfach) distribution."""

    def test_equal_split(self):
        from billing_engine import allocate_energy
        production = pd.Series([10.0])
        consumption = pd.DataFrame({
            "a": [8.0],
            "b": [8.0],
        })
        result = allocate_energy(production, consumption, model="einfach")
        # 10 / 2 = 5 each, both consume >= 5
        assert abs(result["a"].iloc[0] - 5.0) < 0.01
        assert abs(result["b"].iloc[0] - 5.0) < 0.01

    def test_equal_capped_by_consumption(self):
        from billing_engine import allocate_energy
        production = pd.Series([10.0])
        consumption = pd.DataFrame({
            "a": [3.0],
            "b": [8.0],
        })
        result = allocate_energy(production, consumption, model="einfach")
        # Equal share = 5 each, but a only consumes 3, so a=3, remainder to b
        assert abs(result["a"].iloc[0] - 3.0) < 0.01
        assert abs(result["b"].iloc[0] - 7.0) < 0.01


class TestNetworkDiscount:
    """Test Netznutzungsentgelt discount calculation."""

    def test_same_level_40_percent(self):
        from billing_engine import compute_network_discount
        # Same NE7 level: 40% discount
        discount = compute_network_discount(
            allocated_kwh=100.0,
            grid_fee_per_kwh=0.10,
            network_level="same",
        )
        assert abs(discount - 4.0) < 0.01  # 100 * 0.10 * 0.40

    def test_cross_level_20_percent(self):
        from billing_engine import compute_network_discount
        discount = compute_network_discount(
            allocated_kwh=100.0,
            grid_fee_per_kwh=0.10,
            network_level="cross",
        )
        assert abs(discount - 2.0) < 0.01  # 100 * 0.10 * 0.20

    def test_zero_allocation(self):
        from billing_engine import compute_network_discount
        discount = compute_network_discount(0.0, 0.10, "same")
        assert discount == 0.0


class TestBillingPeriodSummary:
    """Test period summary generation."""

    def test_summary_structure(self):
        from billing_engine import generate_billing_summary
        production = pd.Series([10.0, 5.0])
        consumption = pd.DataFrame({
            "a": [4.0, 3.0],
            "b": [6.0, 2.0],
        })
        summary = generate_billing_summary(
            production=production,
            consumption=consumption,
            grid_fee_per_kwh=0.10,
            internal_price_per_kwh=0.15,
            network_level="same",
            distribution_model="proportional",
        )
        assert "participants" in summary
        assert "total_production_kwh" in summary
        assert "total_allocated_kwh" in summary
        assert "total_network_discount_chf" in summary
        assert len(summary["participants"]) == 2


class TestEdgeCases:
    """Edge cases that must not crash."""

    def test_zero_consumption(self):
        from billing_engine import allocate_energy
        production = pd.Series([10.0])
        consumption = pd.DataFrame({"a": [0.0], "b": [0.0]})
        result = allocate_energy(production, consumption, model="proportional")
        assert abs(result["a"].iloc[0]) < 0.01
        assert abs(result["b"].iloc[0]) < 0.01

    def test_zero_production(self):
        from billing_engine import allocate_energy
        production = pd.Series([0.0])
        consumption = pd.DataFrame({"a": [5.0], "b": [3.0]})
        result = allocate_energy(production, consumption, model="proportional")
        assert abs(result["a"].iloc[0]) < 0.01

    def test_single_consumer(self):
        from billing_engine import allocate_energy
        production = pd.Series([10.0])
        consumption = pd.DataFrame({"a": [7.0]})
        result = allocate_energy(production, consumption, model="proportional")
        assert abs(result["a"].iloc[0] - 7.0) < 0.01
