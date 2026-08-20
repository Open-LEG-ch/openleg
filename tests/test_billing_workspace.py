# SPDX-License-Identifier: AGPL-3.0-or-later
"""Behavioral contract for the read-only admin billing workspace."""

from datetime import datetime
from decimal import Decimal
from importlib import import_module
from zoneinfo import ZoneInfo

import pytest


def _module():
    return import_module("billing_workspace")


def test_load_returns_an_honest_empty_state_without_loading_a_detail(monkeypatch):
    workspace = _module()
    monkeypatch.setattr(workspace.db, "list_billing_periods", lambda limit=100: [])

    def unexpected_detail(_period_id):
        raise AssertionError("an empty workspace must not load a detail")

    monkeypatch.setattr(workspace.db, "get_billing_period", unexpected_detail)

    result = workspace.load()

    assert result == {"empty": True, "periods": [], "selected": None}


def test_load_defaults_to_newest_and_builds_a_display_ready_audit_model(monkeypatch):
    workspace = _module()
    start = datetime(2026, 7, 1, tzinfo=ZoneInfo("Europe/Zurich"))
    end = datetime(2026, 8, 1, tzinfo=ZoneInfo("Europe/Zurich"))
    periods = [
        {
            "id": 42,
            "community_id": "community-a",
            "period_start": start,
            "period_end": end,
            "status": "draft",
        },
        {
            "id": 41,
            "community_id": "community-b",
            "period_start": datetime(2026, 6, 1, tzinfo=ZoneInfo("Europe/Zurich")),
            "period_end": datetime(2026, 7, 1, tzinfo=ZoneInfo("Europe/Zurich")),
            "status": "draft",
        },
    ]
    detail = {
        **periods[0],
        "total_production_kwh": Decimal("125.5000"),
        "total_allocated_kwh": Decimal("100.2500"),
        "total_surplus_kwh": Decimal("25.2500"),
        "total_network_discount_chf": Decimal("8.20"),
        "internal_price_chf_per_kwh": Decimal("0.15"),
        "grid_fee_chf_per_kwh": Decimal("0.08"),
        "distribution_model": "proportional",
        "network_level": "same",
        "input_fingerprint": "abc123",
        "source_document_ids": '["E66-A", "E66-B"]',
        "reconciliation": {
            "difference_kwh": Decimal(0),
            "production_difference_kwh": Decimal(0),
            "per_participant": {"consumer-a": {"difference_kwh": Decimal(0)}},
            "production_per_participant": {
                "producer-a": {"difference_kwh": Decimal(0)}
            },
        },
        "line_items": [
            {
                "participant_id": "consumer-a",
                "item_type": "consumer_charge",
                "quantity_kwh": Decimal("100.25"),
                "unit_price_chf_per_kwh": Decimal("0.15"),
                "amount_chf": Decimal("15.0375"),
            },
            {
                "participant_id": "producer-a",
                "item_type": "producer_credit",
                "quantity_kwh": Decimal("100.25"),
                "unit_price_chf_per_kwh": Decimal("0.15"),
                "amount_chf": Decimal("-15.037499"),
            },
            {
                "participant_id": "producer-a",
                "item_type": "rounding_adjustment",
                "quantity_kwh": None,
                "unit_price_chf_per_kwh": None,
                "amount_chf": Decimal("-0.000001"),
            },
        ],
    }
    requested = []
    monkeypatch.setattr(workspace.db, "list_billing_periods", lambda limit=100: periods)
    monkeypatch.setattr(
        workspace.db,
        "get_billing_period",
        lambda period_id: requested.append(period_id) or detail,
    )

    result = workspace.load()
    selected = result["selected"]

    assert requested == [42]
    assert result["empty"] is False
    assert selected["status_label"] == "Entwurf"
    assert "keine definitive Rechnung" in selected["draft_notice"]
    assert selected["period_label"] == "Juli 2026"
    assert selected["tariff"] == {
        "internal_price": "15.00 Rp./kWh",
        "grid_fee": "8.00 Rp./kWh",
        "distribution_model": "Proportional",
        "network_level": "Gleiche Netzebene",
    }
    assert selected["reconciliation"]["balanced"] is True
    assert selected["reconciliation"]["label"] == "Vollständig abgeglichen"
    assert selected["provenance"] == {
        "source_document_ids": ["E66-A", "E66-B"],
        "source_count": 2,
        "input_fingerprint": "abc123",
    }
    assert [item["participant_id"] for item in selected["consumer_charges"]] == [
        "consumer-a"
    ]
    assert [item["participant_id"] for item in selected["producer_credits"]] == [
        "producer-a"
    ]
    assert len(selected["rounding_adjustments"]) == 1
    assert selected["consumer_charges"][0]["amount_chf"] == Decimal("15.0375")
    assert selected["consumer_charges"][0]["display_amount_chf"] == "15.04"
    assert selected["rounding_adjustments"][0]["display_amount_chf"] == "-0.000001"
    assert selected["period_start"] == start.isoformat()


def test_display_values_use_decimal_half_up_rounding(monkeypatch):
    workspace = _module()
    period = {
        "id": 7,
        "community_id": "community-a",
        "period_start": "2026-07-01",
        "status": "draft",
        "total_production_kwh": Decimal("2.675"),
        "line_items": [
            {
                "participant_id": "consumer-a",
                "item_type": "consumer_charge",
                "quantity_kwh": Decimal("2.6755"),
                "unit_price_chf_per_kwh": Decimal("0.02675"),
                "amount_chf": Decimal("2.675"),
            }
        ],
    }
    monkeypatch.setattr(
        workspace.db, "list_billing_periods", lambda limit=100: [period]
    )
    monkeypatch.setattr(workspace.db, "get_billing_period", lambda _period_id: period)

    selected = workspace.load()["selected"]
    item = selected["consumer_charges"][0]

    assert selected["metrics_display"]["production_kwh"] == "2.68"
    assert item["display_quantity_kwh"] == "2.676"
    assert item["display_unit_price_rp"] == "2.68"
    assert item["display_amount_chf"] == "2.68"


def test_explicit_missing_period_fails_instead_of_showing_another_period(
    monkeypatch,
):
    workspace = _module()
    monkeypatch.setattr(
        workspace.db,
        "list_billing_periods",
        lambda limit=100: [{"id": 42, "status": "draft"}],
    )
    monkeypatch.setattr(workspace.db, "get_billing_period", lambda _period_id: None)

    with pytest.raises(workspace.BillingPeriodNotFound):
        workspace.load(period_id=999)


def test_incomplete_reconciliation_and_malformed_provenance_are_visible(
    monkeypatch,
):
    workspace = _module()
    period = {
        "id": 7,
        "community_id": "community-a",
        "period_start": "2026-07-01T00:00:00+02:00",
        "period_end": "2026-08-01T00:00:00+02:00",
        "status": "draft",
        "reconciliation": '{"difference_kwh": 0.25}',
        "source_document_ids": "not-json",
        "line_items": [],
    }
    monkeypatch.setattr(
        workspace.db, "list_billing_periods", lambda limit=100: [period]
    )
    monkeypatch.setattr(workspace.db, "get_billing_period", lambda _period_id: period)

    selected = workspace.load()["selected"]

    assert selected["reconciliation"]["balanced"] is False
    assert selected["reconciliation"]["label"] == "Abweichung prüfen"
    assert selected["provenance"]["source_document_ids"] == []
    assert selected["provenance"]["source_count"] == 0
    assert selected["tariff"]["internal_price"] == "Nicht angegeben"


def test_malformed_persisted_numeric_values_fall_back_without_error(monkeypatch):
    workspace = _module()
    period = {
        "id": 7,
        "community_id": "community-a",
        "period_start": "2026-07-01",
        "status": "draft",
        "total_production_kwh": None,
        "internal_price_chf_per_kwh": "not-a-number",
        "reconciliation": {"difference_kwh": "not-a-number"},
        "line_items": [
            {
                "participant_id": "consumer-a",
                "item_type": "consumer_charge",
                "quantity_kwh": "not-a-number",
                "unit_price_chf_per_kwh": "not-a-number",
                "amount_chf": "not-a-number",
            }
        ],
    }
    monkeypatch.setattr(
        workspace.db, "list_billing_periods", lambda limit=100: [period]
    )
    monkeypatch.setattr(workspace.db, "get_billing_period", lambda _period_id: period)

    selected = workspace.load()["selected"]
    item = selected["consumer_charges"][0]

    assert selected["metrics_display"]["production_kwh"] == "0.00"
    assert selected["tariff"]["internal_price"] == "Nicht angegeben"
    assert selected["reconciliation"]["balanced"] is False
    assert item["display_quantity_kwh"] == "0.000"
    assert item["display_unit_price_rp"] == "0.00"
    assert item["display_amount_chf"] == "0.00"


@pytest.mark.parametrize("non_finite", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_numeric_values_are_never_displayed(monkeypatch, non_finite):
    workspace = _module()
    period = {
        "id": 7,
        "community_id": "community-a",
        "period_start": "2026-07-01",
        "status": "draft",
        "total_production_kwh": non_finite,
        "internal_price_chf_per_kwh": non_finite,
        "line_items": [],
    }
    monkeypatch.setattr(
        workspace.db, "list_billing_periods", lambda limit=100: [period]
    )
    monkeypatch.setattr(workspace.db, "get_billing_period", lambda _period_id: period)

    selected = workspace.load()["selected"]

    assert selected["metrics_display"]["production_kwh"] == "0.00"
    assert selected["tariff"]["internal_price"] == "Nicht angegeben"


def test_rate_uses_explicit_half_up_rounding():
    workspace = _module()

    assert workspace._rate(Decimal("0.15005")) == "15.01 Rp./kWh"
