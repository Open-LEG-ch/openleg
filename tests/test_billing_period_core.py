# SPDX-License-Identifier: AGPL-3.0-or-later
"""TDD contract for auditable draft billing periods."""

from contextlib import contextmanager
from pathlib import Path

import pandas as pd
import pytest

import billing_engine
import database
from store import billing

PROJECT_ROOT = Path(__file__).resolve().parent.parent


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


@pytest.mark.parametrize("price", [float("nan"), float("inf")])
def test_non_finite_billing_prices_are_rejected(price):
    with pytest.raises(ValueError):
        billing_engine.generate_billing_summary(
            production=pd.DataFrame({"producer-a": [1.0]}),
            consumption=pd.DataFrame({"consumer-a": [1.0]}),
            grid_fee_per_kwh=0.10,
            internal_price_per_kwh=price,
            network_level="same",
        )


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
        "line_items": [
            {
                "participant_id": "consumer-a",
                "item_type": "consumer_charge",
                "quantity_kwh": 1,
                "unit_price_chf_per_kwh": 0.15,
                "amount_chf": 0.15,
            },
            {
                "participant_id": "producer-a",
                "item_type": "producer_credit",
                "quantity_kwh": 1,
                "unit_price_chf_per_kwh": 0.15,
                "amount_chf": -0.15,
            },
        ],
    }

    period_id = billing.save_billing_period("community-a", "start", "end", summary)

    assert period_id == 42
    period_query, period_params = cursor.executed[0]
    assert "internal_price_chf_per_kwh" in period_query
    assert "grid_fee_chf_per_kwh" in period_query
    assert "'draft'" in period_query
    assert 0.15 in period_params
    item_queries = cursor.executed[1:]
    assert len(item_queries) == 2
    assert all("item_type" in query for query, _ in item_queries)
    assert item_queries[1][1][-1] == -0.15


def test_schema_keeps_prices_and_signed_line_items_with_the_period():
    schema = (PROJECT_ROOT / "database.py").read_text(encoding="utf-8")

    for column in (
        "internal_price_chf_per_kwh",
        "grid_fee_chf_per_kwh",
        "item_type",
        "quantity_kwh",
        "unit_price_chf_per_kwh",
        "amount_chf",
    ):
        assert column in schema
