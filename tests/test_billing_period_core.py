# SPDX-License-Identifier: AGPL-3.0-or-later
"""TDD contract for auditable draft billing periods."""

import re
from contextlib import contextmanager
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pandas as pd
import pytest

import billing_engine
import database
from store import billing

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _money(value):
    return Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _create_table_block(source, table):
    match = re.search(
        rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\)\s*\"\"\"",
        source,
        flags=re.DOTALL,
    )
    assert match, f"CREATE TABLE {table} block not found"
    return match.group(1)


def _alter_table_block(source, table):
    match = re.search(
        rf"ALTER TABLE {table}\s+(.*?)\s*\"\"\"",
        source,
        flags=re.DOTALL,
    )
    assert match, f"ALTER TABLE {table} block not found"
    return match.group(1)


class _Cursor:
    def __init__(self):
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        return (42,)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _connection_factory(cursor):
    @contextmanager
    def factory():
        yield _Connection(cursor)

    return factory


def test_summary_emits_balanced_consumer_charges_and_producer_credits():
    production = pd.DataFrame({"producer-a": [6.0], "producer-b": [4.0]})
    consumption = pd.DataFrame({"consumer-a": [7.0], "consumer-b": [3.0]})

    summary = billing_engine.generate_billing_summary(
        production=production,
        consumption=consumption,
        grid_fee_per_kwh=0.10,
        internal_price_per_kwh=0.15,
        network_level="same",
    )

    charges = [
        item for item in summary["line_items"] if item["item_type"] == "consumer_charge"
    ]
    credits = [
        item for item in summary["line_items"] if item["item_type"] == "producer_credit"
    ]
    assert {item["participant_id"] for item in charges} == {"consumer-a", "consumer-b"}
    assert {item["participant_id"] for item in credits} == {"producer-a", "producer-b"}
    assert all(item["amount_chf"] > 0 for item in charges)
    assert all(item["amount_chf"] < 0 for item in credits)
    assert round(sum(item["amount_chf"] for item in summary["line_items"]), 6) == 0


def test_persisted_quantities_recompute_to_amounts_with_explicit_rounding_item():
    summary = billing_engine.generate_billing_summary(
        production=pd.DataFrame({"producer-a": [0.000008]}),
        consumption=pd.DataFrame({"consumer-a": [0.000004], "consumer-b": [0.000004]}),
        grid_fee_per_kwh=0.10,
        internal_price_per_kwh=0.625,
        network_level="same",
    )

    priced_items = [
        item
        for item in summary["line_items"]
        if item["item_type"] in {"consumer_charge", "producer_credit"}
    ]
    for item in priced_items:
        expected = _money(
            Decimal(str(item["quantity_kwh"]))
            * Decimal(str(item["unit_price_chf_per_kwh"]))
        )
        assert _money(abs(item["amount_chf"])) == expected

    adjustment = [
        item
        for item in summary["line_items"]
        if item["item_type"] == "rounding_adjustment"
    ]
    assert len(adjustment) == 1
    assert adjustment[0]["participant_id"] == "producer-a"
    assert adjustment[0]["quantity_kwh"] is None
    assert adjustment[0]["unit_price_chf_per_kwh"] is None
    assert _money(sum(item["amount_chf"] for item in summary["line_items"])) == 0


def test_rounding_adjustment_owner_is_independent_of_producer_column_order():
    def adjustment_owner(columns):
        production = pd.DataFrame({key: [value] for key, value in columns})
        summary = billing_engine.generate_billing_summary(
            production=production,
            consumption=pd.DataFrame(
                {"consumer-a": [0.000004], "consumer-b": [0.000004]}
            ),
            grid_fee_per_kwh=0.10,
            internal_price_per_kwh=0.625,
            network_level="same",
        )
        return next(
            item["participant_id"]
            for item in summary["line_items"]
            if item["item_type"] == "rounding_adjustment"
        )

    assert adjustment_owner(
        [("producer-a", 0.000002), ("producer-b", 0.000006)]
    ) == adjustment_owner([("producer-b", 0.000006), ("producer-a", 0.000002)])


def test_anonymous_aggregate_production_does_not_emit_unbalanced_ledger():
    summary = billing_engine.generate_billing_summary(
        production=pd.Series([1.0]),
        consumption=pd.DataFrame({"consumer-a": [1.0]}),
        grid_fee_per_kwh=0.10,
        internal_price_per_kwh=0.15,
        network_level="same",
    )

    assert summary["line_items"] == []


@pytest.mark.parametrize(
    ("production", "consumption", "model"),
    [
        (
            pd.DataFrame({"producer-a": [-1.0]}),
            pd.DataFrame({"consumer-a": [1.0]}),
            "proportional",
        ),
        (
            pd.DataFrame({"producer-a": [1.0]}),
            pd.DataFrame({"consumer-a": [-1.0]}),
            "proportional",
        ),
        (
            pd.DataFrame({"producer-a": [1.0]}),
            pd.DataFrame({"consumer-a": [1.0]}),
            "unknown",
        ),
        (
            pd.DataFrame({"producer-a": [float("inf")]}),
            pd.DataFrame({"consumer-a": [1.0]}),
            "proportional",
        ),
    ],
)
def test_invalid_billing_inputs_are_rejected(production, consumption, model):
    with pytest.raises(ValueError):
        billing_engine.generate_billing_summary(
            production=production,
            consumption=consumption,
            grid_fee_per_kwh=0.10,
            internal_price_per_kwh=0.15,
            network_level="same",
            distribution_model=model,
        )


@pytest.mark.parametrize("price", [float("nan"), float("inf"), None])
def test_non_finite_billing_prices_are_rejected(price):
    with pytest.raises(ValueError):
        billing_engine.generate_billing_summary(
            production=pd.DataFrame({"producer-a": [1.0]}),
            consumption=pd.DataFrame({"consumer-a": [1.0]}),
            grid_fee_per_kwh=0.10,
            internal_price_per_kwh=price,
            network_level="same",
        )


def test_decimal_readings_are_accepted_and_normalized():
    summary = billing_engine.generate_billing_summary(
        production=pd.DataFrame({"producer-a": [Decimal("1.0")]}),
        consumption=pd.DataFrame({"consumer-a": [Decimal("1.0")]}),
        grid_fee_per_kwh=Decimal("0.10"),
        internal_price_per_kwh=Decimal("0.15"),
        network_level="same",
    )

    assert summary["line_items"][0]["amount_chf"] == 0.15


def test_non_numeric_readings_raise_value_error():
    with pytest.raises(ValueError, match="finite and non-negative"):
        billing_engine.generate_billing_summary(
            production=pd.DataFrame({"producer-a": [None]}),
            consumption=pd.DataFrame({"consumer-a": [1.0]}),
            grid_fee_per_kwh=0.10,
            internal_price_per_kwh=0.15,
            network_level="same",
        )


def test_participant_cost_uses_half_up_rounding():
    summary = billing_engine.generate_billing_summary(
        production=pd.DataFrame({"producer-a": [1.5]}),
        consumption=pd.DataFrame({"consumer-a": [1.5]}),
        grid_fee_per_kwh=0.10,
        internal_price_per_kwh=0.15,
        network_level="same",
    )

    assert summary["participants"][0]["internal_cost_chf"] == 0.23


def test_network_discount_uses_half_up_rounding():
    summary = billing_engine.generate_billing_summary(
        production=pd.DataFrame({"producer-a": [1.0]}),
        consumption=pd.DataFrame({"consumer-a": [1.0]}),
        grid_fee_per_kwh=0.0375,
        internal_price_per_kwh=0.15,
        network_level="same",
    )

    assert summary["participants"][0]["network_discount_chf"] == 0.02
    assert summary["total_network_discount_chf"] == 0.02


def test_saved_period_is_draft_with_price_snapshot_and_signed_items(monkeypatch):
    cursor = _Cursor()
    monkeypatch.setattr(database, "get_connection", _connection_factory(cursor))
    summary = {
        "total_production_kwh": 1,
        "total_allocated_kwh": 1,
        "total_surplus_kwh": 0,
        "total_network_discount_chf": 0.04,
        "internal_price_chf_per_kwh": 0.15,
        "grid_fee_chf_per_kwh": 0.10,
        "distribution_model": "proportional",
        "network_level": "same",
        "participants": [
            {
                "id": "consumer-a",
                "consumption_kwh": 1,
                "allocated_kwh": 1,
                "self_supply_ratio": 1,
                "internal_cost_chf": 0.15,
                "network_discount_chf": 0.04,
            }
        ],
        "line_items": [
            {
                "participant_id": "consumer-a",
                "item_type": "consumer_charge",
                "quantity_kwh": 1,
                "unit_price_chf_per_kwh": 0.15,
                "amount_chf": 0.15,
            },
            {
                "participant_id": "consumer-a",
                "item_type": "producer_credit",
                "quantity_kwh": 1,
                "unit_price_chf_per_kwh": 0.15,
                "amount_chf": -0.149999,
            },
            {
                "participant_id": "consumer-a",
                "item_type": "rounding_adjustment",
                "quantity_kwh": None,
                "unit_price_chf_per_kwh": None,
                "amount_chf": -0.000001,
            },
        ],
    }

    period_id = billing.save_billing_period("community-a", "start", "end", summary)

    assert period_id == 42
    period_query, period_params = cursor.executed[0]
    assert "internal_price_chf_per_kwh" in period_query
    assert "grid_fee_chf_per_kwh" in period_query
    assert "'draft'" in period_query
    assert period_params[9:11] == (0.15, 0.10)
    item_queries = cursor.executed[1:]
    assert len(item_queries) == 3
    assert all("item_type" in query for query, _ in item_queries)
    assert "network_discount_chf" in item_queries[0][0]
    assert item_queries[0][1][-5:] == (1, 1, 1, 0.15, 0.04)
    assert item_queries[1][1][5] == -0.149999
    for _, params in item_queries[1:]:
        assert params[-5:] == (None, None, None, None, None)


def test_legacy_summary_still_saves_without_price_snapshot(monkeypatch):
    cursor = _Cursor()
    monkeypatch.setattr(database, "get_connection", _connection_factory(cursor))
    summary = {
        "total_production_kwh": 1,
        "total_allocated_kwh": 1,
        "total_network_discount_chf": 0.04,
        "participants": [
            {
                "id": "consumer-a",
                "consumption_kwh": 1,
                "allocated_kwh": 1,
                "self_supply_ratio": 1,
                "internal_cost_chf": 0.15,
                "network_discount_chf": 0.04,
            }
        ],
    }

    assert billing.save_billing_period("community-a", "start", "end", summary) == 42
    assert cursor.executed[0][1][9:11] == (None, None)
    assert len(cursor.executed) == 2
    assert "consumption_kwh" in cursor.executed[1][0]


def test_schema_keeps_prices_and_signed_line_items_with_the_period():
    schema = (PROJECT_ROOT / "database.py").read_text(encoding="utf-8")
    period_block = _create_table_block(schema, "billing_periods")
    line_item_block = _create_table_block(schema, "billing_line_items")

    for column in ("internal_price_chf_per_kwh", "grid_fee_chf_per_kwh"):
        assert column in period_block
    for column in ("item_type", "quantity_kwh", "unit_price_chf_per_kwh", "amount_chf"):
        assert column in line_item_block


def test_existing_billing_tables_receive_all_new_columns_additively():
    schema = (PROJECT_ROOT / "database.py").read_text(encoding="utf-8")
    period_migration = _alter_table_block(schema, "billing_periods")
    line_item_migration = _alter_table_block(schema, "billing_line_items")

    for column in (
        "internal_price_chf_per_kwh",
        "grid_fee_chf_per_kwh",
        "timezone",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in period_migration
    for column in (
        "item_type",
        "quantity_kwh",
        "unit_price_chf_per_kwh",
        "amount_chf",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in line_item_migration
