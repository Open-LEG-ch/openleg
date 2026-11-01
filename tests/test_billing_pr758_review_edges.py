# SPDX-License-Identifier: AGPL-3.0-or-later
"""Behavioral regressions for the final PR #758 app review."""

from copy import deepcopy
from decimal import Decimal

import pandas as pd
import pytest

import billing_engine
import billing_readings
import billing_runner
import billing_workspace
import dashboard
import vnb_calculated_values
from tests import test_billing_readings as readings
from tests import test_billing_runner_accepted_split as accepted
from tests.test_billing_approval import _draft
from tests.test_vnb_calculated_values import Repository, delivery

accepted_split = accepted.accepted_split
rows = readings.rows


def test_explicit_billed_ties_use_half_up_for_both_sides():
    summary = billing_engine.generate_billing_summary(
        pd.DataFrame({"p": [1.0]}),
        pd.DataFrame({"a": [1.0]}),
        0.08,
        0.12,
        "same",
        billed_allocation={
            "consumption_kwh": {"a": "0.5000005"},
            "production_kwh": {"p": "0.5000005"},
        },
    )
    assert {
        Decimal(str(row["quantity_kwh"]))
        for row in summary["line_items"]
        if row["item_type"] in {"consumer_charge", "producer_credit"}
    } == {Decimal("0.500001")}


@pytest.mark.parametrize(
    "unit,value", [("kWh", "0.0000004"), ("kWh", "0.5000005"), ("Wh", "0.0004")]
)
def test_json_exchange_reports_sub_micro_quantity_precision(unit, value):
    payload = delivery(unit)
    for row in payload["records"]:
        row["value"] = value
    result = vnb_calculated_values.accept_delivery(
        payload,
        transport="file",
        evidence=b"synthetic fine precision",
        repository=Repository(),
    )
    assert result["status"] != "accepted"
    assert any(
        d["code"] == "unsupported_quantity_precision" for d in result["diagnostics"]
    )
    assert result["normalized_records"] == []


@pytest.mark.parametrize(
    "unit,value",
    [("kWh", "0.000001"), ("kWh", "0.5000000"), ("Wh", "0.001"), ("kWh", "0")],
)
def test_json_exchange_retains_exactly_representable_quantities(unit, value):
    payload = delivery(unit)
    for row in payload["records"]:
        row["value"] = value
    result = vnb_calculated_values.accept_delivery(
        payload,
        transport="file",
        evidence=b"synthetic supported precision",
        repository=Repository(),
    )
    assert result["status"] == "accepted"


@pytest.mark.parametrize(
    "field,value",
    [
        ("vnb_imbalance_kwh", "0.001"),
        ("vnb_imbalance_ratio", "0.001"),
        ("vnb_imbalance_kwh", "NaN"),
        ("vnb_imbalance_kwh", None),
        ("vnb_imbalance_tolerance_ratio", 0.001),
        ("vnb_imbalance_release", {"id": 7}),
    ],
)
def test_obsolete_or_unequal_source_draft_has_no_approve_action(field, value):
    draft = _draft()
    draft["reconciliation"].update(
        precision_policy={"format_version": "e66/1"},
        vnb_imbalance_kwh=0,
        vnb_imbalance_ratio=0,
    )
    draft["reconciliation"][field] = value
    original = deepcopy(draft)
    assert billing_workspace.readiness_flags(draft)["reconciled"] is False
    assert dashboard._billing_workspace_period(draft)["approvable"] is False
    assert draft == original


@pytest.mark.parametrize("changed", [False, True])
def test_battery_physical_totals_must_match_accepted_evidence(
    monkeypatch, accepted_split, changed
):
    source, snapshot, _, saved = accepted_split
    monkeypatch.setattr(
        billing_runner.db,
        "get_battery",
        lambda *_: {
            "participant_id": "p",
            "capacity_kwh": 10,
            "annual_cost_chf": 0,
            "shares": {"a": 50, "b": 50},
        },
    )
    if changed:
        snapshot["readings"][-1]["total_kwh"] += Decimal("0.003")
        snapshot["readings"][-1]["grid_kwh"] += Decimal("0.003")
        with pytest.raises(
            billing_runner.BillingRunError, match="Physical readings"
        ) as refused:
            accepted._run(source)
        assert refused.value.code == "vnb_allocation_mismatch"
        saved.assert_not_called()
    else:
        assert accepted._run(source)["status"] == "created"
        saved.assert_called_once()


@pytest.mark.parametrize("channel", ["total_kwh", "grid_kwh", "community_kwh"])
@pytest.mark.parametrize(
    "value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")]
)
def test_nonfinite_raw_readings_report_data_problems(monkeypatch, rows, channel, value):
    rows[0][channel] = value
    readings._install(monkeypatch, readings._points(), rows)
    with pytest.raises(billing_readings.PeriodDataError) as refused:
        billing_readings.load_period_frames(
            readings.COMMUNITY, readings.PERIOD_START, readings.PERIOD_END
        )
    assert any(
        p["kind"] == "non_finite_value" and channel in p["detail"]
        for p in refused.value.problems
    )
