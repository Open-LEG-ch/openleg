# SPDX-License-Identifier: AGPL-3.0-or-later
"""Price validated VNB quantities with the existing tariff and fee rules."""

from decimal import Decimal

import pandas as pd


def test_vnb_consumer_and_producer_quantities_supply_energy_and_settlement_prices():
    import billing_engine

    index = pd.date_range("2026-10-01", periods=1, freq="15min", tz="UTC")
    consumption = pd.DataFrame({"a": [1], "b": [2], "p": [0]}, index=index)
    production = pd.DataFrame({"a": [0], "b": [0], "p": [1]}, index=index)
    vnb_consumption = pd.DataFrame(
        {"a": [Decimal("0.333")], "b": [Decimal("0.667")], "p": [Decimal(0)]},
        index=index,
    )
    vnb_production = pd.DataFrame(
        {"a": [Decimal(0)], "b": [Decimal(0)], "p": [Decimal(1)]}, index=index
    )

    summary = billing_engine.generate_vnb_billing_summary(
        production,
        consumption,
        vnb_consumption,
        vnb_production,
        grid_fee_per_kwh="0.08",
        internal_price_per_kwh="0.12",
        network_level="same",
        settlement_fee_per_kwh="0.01",
    )

    lines = {
        (item["participant_id"], item["item_type"]): item
        for item in summary["line_items"]
    }
    assert Decimal(str(lines[("a", "consumer_charge")]["quantity_kwh"])) == Decimal(
        "0.333"
    )
    assert Decimal(str(lines[("b", "consumer_charge")]["quantity_kwh"])) == Decimal(
        "0.667"
    )
    assert Decimal(str(lines[("p", "producer_credit")]["quantity_kwh"])) == Decimal(1)
    assert Decimal(str(lines[("a", "consumer_charge")]["amount_chf"])) == Decimal(
        "0.039960"
    )
    assert Decimal(str(lines[("b", "consumer_charge")]["amount_chf"])) == Decimal(
        "0.080040"
    )
    assert Decimal(str(lines[("a", "settlement_fee")]["amount_chf"])) == Decimal(
        "0.003330"
    )
    assert Decimal(str(lines[("b", "settlement_fee")]["amount_chf"])) == Decimal(
        "0.006670"
    )
    assert Decimal(str(lines[("p", "producer_credit")]["amount_chf"])) == Decimal(
        "-0.120000"
    )
