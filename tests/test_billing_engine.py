# SPDX-License-Identifier: AGPL-3.0-or-later
"""TDD tests for billing_engine.py - 15-min interval energy allocation."""

import pandas as pd
import pytest


class TestProportionalAllocation:
    """Test proportional distribution of solar production."""

    def test_basic_proportional(self):
        from billing_engine import allocate_energy

        # 2 consumers, 1 producer, single 15-min interval
        production = pd.Series([10.0])  # 10 kWh produced
        consumption = pd.DataFrame(
            {
                "consumer_a": [6.0],
                "consumer_b": [4.0],
            }
        )
        result = allocate_energy(production, consumption, model="proportional")
        # consumer_a gets 60% of 10 = 6, consumer_b gets 40% of 10 = 4
        assert abs(result["consumer_a"].iloc[0] - 6.0) < 0.01
        assert abs(result["consumer_b"].iloc[0] - 4.0) < 0.01

    def test_production_exceeds_consumption(self):
        from billing_engine import allocate_energy

        production = pd.Series([20.0])
        consumption = pd.DataFrame(
            {
                "consumer_a": [6.0],
                "consumer_b": [4.0],
            }
        )
        result = allocate_energy(production, consumption, model="proportional")
        # Can't allocate more than consumed: a=6, b=4
        assert abs(result["consumer_a"].iloc[0] - 6.0) < 0.01
        assert abs(result["consumer_b"].iloc[0] - 4.0) < 0.01

    def test_production_less_than_consumption(self):
        from billing_engine import allocate_energy

        production = pd.Series([5.0])
        consumption = pd.DataFrame(
            {
                "consumer_a": [6.0],
                "consumer_b": [4.0],
            }
        )
        result = allocate_energy(production, consumption, model="proportional")
        # 5 kWh split proportionally: a=3, b=2
        assert abs(result["consumer_a"].iloc[0] - 3.0) < 0.01
        assert abs(result["consumer_b"].iloc[0] - 2.0) < 0.01

    def test_multiple_intervals(self):
        from billing_engine import allocate_energy

        production = pd.Series([10.0, 5.0, 0.0])
        consumption = pd.DataFrame(
            {
                "a": [4.0, 3.0, 2.0],
                "b": [6.0, 2.0, 3.0],
            }
        )
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
        consumption = pd.DataFrame(
            {
                "a": [8.0],
                "b": [8.0],
            }
        )
        result = allocate_energy(production, consumption, model="einfach")
        # 10 / 2 = 5 each, both consume >= 5
        assert abs(result["a"].iloc[0] - 5.0) < 0.01
        assert abs(result["b"].iloc[0] - 5.0) < 0.01

    def test_equal_capped_by_consumption(self):
        from billing_engine import allocate_energy

        production = pd.Series([10.0])
        consumption = pd.DataFrame(
            {
                "a": [3.0],
                "b": [8.0],
            }
        )
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
        consumption = pd.DataFrame(
            {
                "a": [4.0, 3.0],
                "b": [6.0, 2.0],
            }
        )
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


class TestSettlementFee:
    """The VNB settlement fee applies to the energy settled through the VNB."""

    _UNSET = object()

    def _summary(self, settlement_fee_per_kwh=_UNSET, **overrides):
        from billing_engine import generate_billing_summary

        production = overrides.pop("production", pd.DataFrame({"producer": [10.0]}))
        consumption = overrides.pop(
            "consumption", pd.DataFrame({"consumer_a": [6.0], "consumer_b": [4.0]})
        )
        kwargs = {
            "grid_fee_per_kwh": 0.08,
            "internal_price_per_kwh": 0.15,
            "network_level": "same",
            "distribution_model": "proportional",
        }
        kwargs.update(overrides)
        if settlement_fee_per_kwh is not self._UNSET:
            kwargs["settlement_fee_per_kwh"] = settlement_fee_per_kwh
        return generate_billing_summary(production, consumption, **kwargs)

    def test_fee_applies_to_allocated_energy_per_participant(self):
        summary = self._summary(settlement_fee_per_kwh=0.02)

        assert summary["settlement_fee_chf_per_kwh"] == 0.02
        assert summary["total_settlement_fee_chf"] == 0.20
        participants = {p["id"]: p for p in summary["participants"]}
        assert participants["consumer_a"]["settlement_fee_chf"] == 0.12
        assert participants["consumer_b"]["settlement_fee_chf"] == 0.08
        fee_items = [
            item
            for item in summary["line_items"]
            if item["item_type"] == "settlement_fee"
        ]
        assert {(i["participant_id"], i["quantity_kwh"]) for i in fee_items} == {
            ("consumer_a", 6.0),
            ("consumer_b", 4.0),
        }
        assert all(i["unit_price_chf_per_kwh"] == 0.02 for i in fee_items)
        assert {i["participant_id"]: i["amount_chf"] for i in fee_items} == {
            "consumer_a": 0.12,
            "consumer_b": 0.08,
        }

    def test_fee_quantity_matches_the_consumer_charge_quantity(self):
        summary = self._summary(settlement_fee_per_kwh=0.02)

        charges = {
            i["participant_id"]: i["quantity_kwh"]
            for i in summary["line_items"]
            if i["item_type"] == "consumer_charge"
        }
        fees = {
            i["participant_id"]: i["quantity_kwh"]
            for i in summary["line_items"]
            if i["item_type"] == "settlement_fee"
        }
        assert fees == charges

    def test_rounding_adjustment_closes_the_internal_pool_only(self):
        summary = self._summary(
            settlement_fee_per_kwh=0.02,
            production=pd.DataFrame({"producer": [0.268197]}),
            consumption=pd.DataFrame({"a": [0.532064], "b": [0.085621]}),
        )
        fee_total = sum(
            i["amount_chf"]
            for i in summary["line_items"]
            if i["item_type"] == "settlement_fee"
        )
        rounding = [
            i["amount_chf"]
            for i in summary["line_items"]
            if i["item_type"] == "rounding_adjustment"
        ]
        charges = sum(
            i["amount_chf"]
            for i in summary["line_items"]
            if i["item_type"] == "consumer_charge"
        )
        credits = sum(
            i["amount_chf"]
            for i in summary["line_items"]
            if i["item_type"] == "producer_credit"
        )
        assert rounding[0] == round(-(charges + credits), 6)
        assert fee_total > 0

    def test_without_the_fee_the_draft_stays_in_the_legacy_shape(self):
        summary = self._summary()

        assert "settlement_fee_chf_per_kwh" not in summary
        assert "total_settlement_fee_chf" not in summary
        for participant in summary["participants"]:
            assert "settlement_fee_chf" not in participant
        assert all(
            item["item_type"] != "settlement_fee" for item in summary["line_items"]
        )

    def test_zero_fee_behaves_like_no_fee(self):
        from decimal import Decimal

        baseline = self._summary(settlement_fee_per_kwh=0.0)
        assert "settlement_fee_chf_per_kwh" not in baseline
        assert all(
            item["item_type"] != "settlement_fee" for item in baseline["line_items"]
        )
        assert baseline["total_allocated_kwh"] == self._summary()["total_allocated_kwh"]
        assert Decimal(str(baseline["total_network_discount_chf"])) == Decimal(
            str(self._summary()["total_network_discount_chf"])
        )

    @pytest.mark.parametrize(
        "fee", [-0.01, float("nan"), float("inf"), float("-inf"), "abc", None]
    )
    def test_invalid_fee_fails_closed(self, fee):
        with pytest.raises(ValueError):
            self._summary(settlement_fee_per_kwh=fee)

    def test_decimal_fee_is_accepted(self):
        from decimal import Decimal

        summary = self._summary(settlement_fee_per_kwh=Decimal("0.02"))
        assert summary["settlement_fee_chf_per_kwh"] == 0.02
