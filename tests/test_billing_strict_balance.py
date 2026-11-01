# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exact VNB balance is required even when every source channel is plausible."""

import json
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import billing_approval
import billing_readings
import billing_reconciliation
import billing_runner
import database
from tests.test_billing_reconciliation import evidence
from tests.test_billing_runner import COMMUNITY, DEFAULT_POLICY

FROZEN = Path(__file__).parent / "fixtures" / "pr758_unequal_proposal_drafts.json"
MODELS = ["proportional", "einfach"]
DIRECTIONS = ["production_more", "consumption_more"]


@pytest.mark.parametrize("allowance", [0.001, 0, "invalid", "NaN"])
def test_obsolete_default_allowance_cannot_approve_unequal_json_source(allowance):
    from tests.test_billing_approval import _draft

    draft = _draft(period_end="2026-01-01T00:15:00+01:00")
    draft["calculated_values_fingerprint"] = "b" * 64
    draft["reconciliation"].update(
        vnb_imbalance_tolerance_ratio=allowance,
        vnb_delivered_production_kwh=13.36767,
        vnb_imbalance_kwh=0.001,
        vnb_imbalance_ratio=0.00007481,
    )
    source = {
        "community_id": draft["community_id"],
        "period_start": draft["period_start"],
        "period_end": draft["period_end"],
        "fingerprint": draft["calculated_values_fingerprint"],
        "format_version": "json/1",
        "status": "accepted",
        "normalized_records": [
            {
                "participant_id": participant,
                "direction": direction,
                "measured_at": draft["period_start"],
                "allocated_kwh": quantity,
            }
            for participant, direction, quantity in (
                ("building-a", "consumption", 13.366667),
                ("building-c", "consumption", 0.000003),
                ("building-b", "production", 13.367670),
            )
        ],
    }
    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(
            draft, issue_date=date(2026, 2, 5), calculated_values=source
        )


def test_even_a_balanced_draft_with_an_obsolete_release_requires_repreparation():
    from tests.test_billing_approval import _draft

    draft = _draft()
    draft["reconciliation"].update(
        vnb_imbalance_tolerance_ratio=0.001,
        vnb_imbalance_release={
            "id": 7,
            "max_imbalance_ratio": "0.001",
            "reason": "Private obsolete exception reason",
            "released_by": "preparer",
            "released_at": "2026-02-03T10:00:00+01:00",
        },
    )
    original = deepcopy(draft)
    with pytest.raises(billing_approval.BillingApprovalError):
        billing_approval.prepare_invoice_snapshots(draft, issue_date=date(2026, 2, 5))
    assert draft == original


def _source(direction):
    # Three independently rounded thirds leave 0.001 kWh in either direction.
    # Both certificates support these vectors; neither certifies exact balance.
    produced, local = {
        "production_more": ("4.000", "1.333"),
        "consumption_more": ("5.000", "1.667"),
        "balanced": ("6.000", "2.000"),
    }[direction]
    return evidence(
        [
            (p, "consumption", [("4.000", local, str(Decimal(4) - Decimal(local)))])
            for p in ("a", "b", "c")
        ]
        + [("p", "production", [(produced, produced, "0.000")])]
    )


def _install(monkeypatch, source, model):
    """Replace DB boundaries only; keep loading, certification and pricing real."""
    source.update(
        community_id=COMMUNITY, vnb_case_id="strict-balance", source="Synthetic VNB"
    )
    rows = [
        {
            "metering_point_id": row["source_metering_point_id"],
            "direction": row["direction"],
            "measured_at": datetime.fromisoformat(row["measured_at"]),
            "resolution_minutes": 15,
            "source_document_id": "synthetic-e66",
            **{k: Decimal(v) for k, v in row["source_quantities"].items()},
        }
        for row in source["normalized_records"]
    ]
    snapshot = {
        "points": [
            {**mapping, "member_status": "confirmed"}
            for mapping in source["source_metadata"]["mappings"]
        ],
        "readings": rows,
        "unassigned_point_ids": [],
    }
    monkeypatch.setattr(database, "is_db_available", lambda: True)
    monkeypatch.setattr(
        database,
        "get_billing_policy",
        lambda *_: {
            **DEFAULT_POLICY,
            "distribution_model": model,
            "effective_from": "2026-01-01T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(database, "get_battery", lambda *_: None)
    # Keep stale records available after the release API itself is removed.
    monkeypatch.setattr(
        database,
        "get_billing_imbalance_release",
        MagicMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        database, "record_billing_imbalance_block", MagicMock(), raising=False
    )
    monkeypatch.setattr(database, "get_billable_period_snapshot", lambda *_: snapshot)
    monkeypatch.setattr(database, "get_validated_calculated_values", lambda *_: source)
    monkeypatch.setattr(database, "get_billing_period_for_window", lambda *_: None)
    saved = MagicMock(return_value=42)
    invoices = MagicMock()
    monkeypatch.setattr(database, "save_billing_period", saved)
    monkeypatch.setattr(database, "approve_billing_period", invoices)
    return rows, saved, invoices


def _run(source):
    return billing_runner.run_billing_period(
        COMMUNITY, source["period_start"], source["period_end"]
    )


def _totals(source):
    return {
        direction: sum(
            (
                Decimal(row["source_quantities"]["community_kwh"])
                for row in source["normalized_records"]
                if row["direction"] == direction
            ),
            Decimal(0),
        )
        for direction in ("consumption", "production")
    }


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("direction", DIRECTIONS)
@pytest.mark.parametrize("raw_split", ["accepted", "balanced", "worse"])
def test_certified_unequal_accepted_source_never_saves_a_draft(
    monkeypatch, model, direction, raw_split
):
    source = _source(direction)
    certificate = billing_reconciliation.audit_e66(source, model=model)
    assert certificate["outcome"] == "rounding_compatible"
    totals = _totals(source)
    gap = totals["production"] - totals["consumption"]
    assert abs(gap) == Decimal("0.001")
    assert abs(gap) / totals["consumption"] < Decimal("0.001")
    rows, saved, invoices = _install(monkeypatch, source, model)
    if raw_split != "accepted":
        # Change only the imported split. The accepted quantities stay intact.
        adjustment = gap if raw_split == "balanced" else -gap * 10
        rows[0]["community_kwh"] += adjustment
        rows[0]["grid_kwh"] -= adjustment
        raw_totals = {
            d: sum(row["community_kwh"] for row in rows if row["direction"] == d)
            for d in ("consumption", "production")
        }
        raw_gap = raw_totals["production"] - raw_totals["consumption"]
        if raw_split == "balanced":
            assert raw_gap == 0
        else:
            assert abs(raw_gap) > abs(gap)
    assert _totals(source) == totals

    with pytest.raises(billing_runner.BillingRunError) as refused:
        _run(source)

    assert refused.value.code == "vnb_allocation_mismatch"
    assert refused.value.reason == "vnb_balancing_policy_required"
    saved.assert_not_called()
    invoices.assert_not_called()


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("direction", DIRECTIONS)
def test_a_stored_period_release_cannot_authorize_unequal_energy(
    monkeypatch, model, direction
):
    source = _source(direction)
    assert (
        billing_reconciliation.audit_e66(source, model=model)["outcome"]
        == "rounding_compatible"
    )
    _, saved, invoices = _install(monkeypatch, source, model)
    database.get_billing_imbalance_release.return_value = {
        "id": 7,
        "max_imbalance_ratio": Decimal("0.02"),
        "reason": "Obsolete proposal release",
        "released_by": "preparer",
        "released_at": source["period_end"],
    }

    with pytest.raises(billing_runner.BillingRunError) as refused:
        _run(source)

    assert refused.value.code == "vnb_allocation_mismatch"
    assert refused.value.reason == "vnb_balancing_policy_required"
    saved.assert_not_called()
    invoices.assert_not_called()


@pytest.mark.parametrize("direction", ["consumption", "production"])
def test_read_frame_refuses_a_gap_hidden_by_six_decimal_display(monkeypatch, direction):
    # E66's certified 0.001 kWh grid cannot express a sub-micro-kWh gap.
    # Ordinary read-frame decimals must be checked before aggregate rounding.
    source = _source("balanced")
    rows, _, _ = _install(monkeypatch, source, "proportional")
    row = next(row for row in rows if row["direction"] == direction)
    row["community_kwh"] -= Decimal("0.0000001")
    row["grid_kwh"] += Decimal("0.0000001")
    totals = {
        d: sum(r["community_kwh"] for r in rows if r["direction"] == d)
        for d in ("consumption", "production")
    }
    assert totals["consumption"] != totals["production"]
    assert round(totals["consumption"], 6) == round(totals["production"], 6)

    with pytest.raises(billing_readings.PeriodDataError) as refused:
        billing_readings.load_period_frames(
            COMMUNITY, source["period_start"], source["period_end"]
        )

    assert any(p["kind"] == "vnb_imbalance" for p in refused.value.problems)


@pytest.mark.parametrize("direction", ["consumption", "production"])
def test_one_sided_read_frame_cannot_be_billed(monkeypatch, direction):
    # A positive one-sided split has no E66 model witness. Exercise the loader
    # directly without claiming a certificate for physically impossible energy.
    source = evidence([(direction, direction, [("0.001", "0.001", "0.000")])])
    _, saved, invoices = _install(monkeypatch, source, "proportional")

    with pytest.raises(billing_readings.PeriodDataError) as refused:
        billing_readings.load_period_frames(
            COMMUNITY, source["period_start"], source["period_end"]
        )

    assert any(p["kind"] == "vnb_imbalance" for p in refused.value.problems)
    with pytest.raises(billing_runner.BillingRunError):
        _run(source)
    saved.assert_not_called()
    invoices.assert_not_called()


@pytest.mark.parametrize("model", MODELS)
def test_zero_zero_certified_source_remains_valid(monkeypatch, model):
    source = evidence(
        [
            ("c", "consumption", [("0.000", "0.000", "0.000")]),
            ("p", "production", [("0.000", "0.000", "0.000")]),
        ]
    )
    assert (
        billing_reconciliation.audit_e66(source, model=model)["outcome"]
        == "rounding_compatible"
    )
    _, saved, invoices = _install(monkeypatch, source, model)

    assert _run(source) == {"status": "created", "period_id": 42}

    assert all(
        Decimal(str(item["quantity_kwh"])) == 0
        for item in saved.call_args.args[3]["line_items"]
    )
    invoices.assert_not_called()


@pytest.mark.parametrize("model", MODELS)
def test_balanced_source_preserves_each_producer_quantity_verbatim(monkeypatch, model):
    source = evidence(
        [
            ("c", "consumption", [("1.000", "1.000", "0.000")]),
            ("p", "production", [("1.000", "0.333", "0.667")]),
            ("q", "production", [("2.000", "0.667", "1.333")]),
        ]
    )
    assert (
        billing_reconciliation.audit_e66(source, model=model)["outcome"]
        == "rounding_compatible"
    )
    _, saved, _ = _install(monkeypatch, source, model)

    assert _run(source)["status"] == "created"

    draft = saved.call_args.args[3]
    assert {
        row["participant_id"]: Decimal(str(row["quantity_kwh"]))
        for row in draft["line_items"]
        if row["item_type"] == "producer_credit"
    } == {"c": Decimal(0), "p": Decimal("0.333"), "q": Decimal("0.667")}
    snapshots = billing_approval.prepare_invoice_snapshots(
        {
            **draft,
            "id": 42,
            "community_id": COMMUNITY,
            "status": "draft",
            "period_start": source["period_start"],
            "period_end": source["period_end"],
        },
        issue_date=date(2026, 10, 2),
        calculated_values=source,
    )
    assert {row["participant_id"] for row in snapshots} == {"c", "p", "q"}


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("direction", DIRECTIONS)
@pytest.mark.parametrize(
    "allowance", ["frozen_default", "old_release", "removed_allowance"]
)
def test_independently_frozen_unequal_proposal_is_not_approvable(
    model, direction, allowance
):
    # Captured once from 6766942. Never create the expected draft with the
    # runner here: after strict refusal the historic draft must still be tested.
    captured = json.loads(FROZEN.read_text())
    assert captured["captured_at_commit"] == "67669420866be690d81478dcd9f7d43f14675e25"
    assert captured["synthetic_only"] is True
    fixture = captured["candidates"][f"{model}/{direction}"]
    source, draft = deepcopy(fixture["source"]), deepcopy(fixture["draft"])
    assert (
        billing_reconciliation.audit_e66(source, model=model)
        == draft["reconciliation"]["precision_policy"]
    )
    totals = _totals(source)
    assert abs(totals["production"] - totals["consumption"]) == Decimal("0.001")
    assert (
        sum(
            Decimal(str(r["quantity_kwh"]))
            for r in draft["line_items"]
            if r["item_type"] == "producer_credit"
        )
        == totals["consumption"]
    )
    if allowance == "old_release":
        draft["reconciliation"]["vnb_imbalance_tolerance_ratio"] = 0.02
        draft["reconciliation"]["vnb_imbalance_release"] = {
            "id": 7,
            "max_imbalance_ratio": "0.02",
            "reason": "Obsolete proposal release",
            "released_by": "preparer",
            "released_at": "2026-10-01T00:15:00+00:00",
        }
    elif allowance == "removed_allowance":
        draft["reconciliation"].pop("vnb_imbalance_tolerance_ratio")

    message = (
        "VNB-Beleg" if allowance == "removed_allowance" else "veraltete Toleranzregel"
    )
    with pytest.raises(billing_approval.BillingApprovalError, match=message):
        billing_approval.prepare_invoice_snapshots(
            draft, issue_date=date(2026, 10, 2), calculated_values=source
        )
