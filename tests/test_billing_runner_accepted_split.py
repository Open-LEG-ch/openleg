# SPDX-License-Identifier: AGPL-3.0-or-later
"""Accepted source replaces the allocation claim, never the physical checks."""

from decimal import Decimal
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

import billing_readings
import billing_reconciliation
import billing_runner
from tests.test_billing_reconciliation import evidence
from tests.test_billing_runner import DEFAULT_POLICY

COMMUNITY = "accepted-split-leg"


@pytest.fixture
def accepted_split(monkeypatch):
    # Consumption is limiting: the accepted source consumes 1.000 of the
    # producer's 1.003 kWh. Every channel uses the E66 0.001 kWh grid.
    delivery = evidence(
        [
            ("a", "consumption", [("0.333", "0.333", "0.000")]),
            ("b", "consumption", [("0.667", "0.667", "0.000")]),
            ("p", "production", [("1.003", "1.000", "0.003")]),
        ]
    )
    delivery.update(vnb_case_id="corrected-e66", source="Synthetic VNB correction")
    assert (
        billing_reconciliation.audit_e66(delivery, model="proportional")["outcome"]
        == "rounding_compatible"
    )
    snapshot = {"points": [], "readings": [], "unassigned_point_ids": []}
    for record in delivery["normalized_records"]:
        point = record["source_metering_point_id"]
        snapshot["points"].append(
            {
                "metering_point_id": point,
                "building_id": record["participant_id"],
                "member_status": "confirmed",
                "expected_directions": [record["direction"]],
            }
        )
        snapshot["readings"].append(
            {
                "metering_point_id": point,
                "direction": record["direction"],
                "measured_at": delivery["period_start"],
                "resolution_minutes": 15,
                "source_document_id": "imported-e66",
                **{
                    key: Decimal(value)
                    for key, value in record["source_quantities"].items()
                },
            }
        )
    db = billing_runner.db
    monkeypatch.setattr(db, "is_db_available", lambda: True)
    monkeypatch.setattr(
        db, "get_billing_policy", MagicMock(return_value=DEFAULT_POLICY)
    )
    monkeypatch.setattr(db, "get_battery", MagicMock(return_value=None))
    monkeypatch.setattr(
        db, "get_billable_period_snapshot", MagicMock(return_value=snapshot)
    )
    fetched = MagicMock(return_value=delivery)
    saved = MagicMock(return_value=42)
    monkeypatch.setattr(db, "get_validated_calculated_values", fetched)
    monkeypatch.setattr(
        db, "get_billing_period_for_window", MagicMock(return_value=None)
    )
    monkeypatch.setattr(db, "save_billing_period", saved)
    return delivery, snapshot, fetched, saved


def _raw_production(snapshot, local):
    row = snapshot["readings"][-1]
    row["community_kwh"] = Decimal(local)
    row["grid_kwh"] = row["total_kwh"] - row["community_kwh"]


def _run(delivery):
    return billing_runner.run_billing_period(
        COMMUNITY, delivery["period_start"], delivery["period_end"]
    )


@pytest.mark.parametrize("raw_local", ["1.001", "1.003"], ids=["within", "outside"])
def test_runner_bills_the_balanced_accepted_split_despite_raw_residue(
    accepted_split, raw_local
):
    delivery, snapshot, fetched, saved = accepted_split
    _raw_production(snapshot, raw_local)

    assert _run(delivery) == {"status": "created", "period_id": 42}

    fetched.assert_called_once_with(
        COMMUNITY, delivery["period_start"], delivery["period_end"]
    )
    summary = saved.call_args.args[3]
    assert summary["calculated_values_fingerprint"] == delivery["fingerprint"]
    reconciliation = summary["reconciliation"]
    assert reconciliation["vnb_imbalance_kwh"] == 0
    assert reconciliation["precision_policy"]["outcome"] == "rounding_compatible"
    for kind in ("consumer_charge", "producer_credit"):
        assert sum(
            Decimal(str(row["quantity_kwh"]))
            for row in summary["line_items"]
            if row["item_type"] == kind
        ) == Decimal("1.000")


@pytest.mark.parametrize("raw_local", ["1.001", "1.003"])
def test_missing_accepted_source_refuses_even_when_raw_split_is_bad(
    accepted_split, raw_local
):
    delivery, snapshot, fetched, saved = accepted_split
    _raw_production(snapshot, raw_local)
    fetched.return_value = None

    with pytest.raises(billing_runner.BillingRunError) as error:
        _run(delivery)

    assert error.value.code == "vnb_evidence_missing"
    saved.assert_not_called()


def _unbalanced_replacement(delivery, snapshot):
    # This vector is still rounding-compatible; its 0.999/1.000 accepted
    # split is unequal and must refuse even when the imported split differs.
    delivery.update(
        evidence(
            [
                ("a", "consumption", [("1.000", "0.333", "0.667")]),
                ("b", "consumption", [("2.000", "0.666", "1.334")]),
                ("p", "production", [("1.000", "1.000", "0.000")]),
            ]
        )
    )
    for raw, accepted in zip(snapshot["readings"], delivery["normalized_records"]):
        raw.update(
            {
                key: Decimal(value)
                for key, value in accepted["source_quantities"].items()
            }
        )
    # The imported .996/1.000 split differs from the accepted .999/1.000.
    snapshot["readings"][0].update(
        community_kwh=Decimal("0.330"), grid_kwh=Decimal("0.670")
    )
    assert (
        billing_reconciliation.audit_e66(delivery, model="proportional")["outcome"]
        == "rounding_compatible"
    )


def test_unbalanced_accepted_replacement_refuses_its_own_quantities(accepted_split):
    delivery, snapshot, _, saved = accepted_split
    _unbalanced_replacement(delivery, snapshot)

    with pytest.raises(billing_runner.BillingRunError) as error:
        _run(delivery)

    assert error.value.code == "vnb_allocation_mismatch"
    assert error.value.reason == "vnb_balancing_policy_required"
    saved.assert_not_called()


@pytest.mark.parametrize(
    "fault,message",
    [
        ("physical", "Physical readings differ from accepted source"),
        ("identity", "incomplete_vnb_source"),
        ("precision", "invalid_vnb_precision"),
        ("normalization", "inconsistent_vnb_source"),
    ],
)
@pytest.mark.parametrize("raw_local", ["1.001", "1.003"])
def test_accepted_source_safeguards_still_refuse_after_raw_gate_is_deferred(
    accepted_split, fault, message, raw_local
):
    delivery, snapshot, _, saved = accepted_split
    _raw_production(snapshot, raw_local)
    if fault == "physical":
        snapshot["readings"][0]["total_kwh"] += Decimal("0.001")
    elif fault == "identity":
        delivery["normalized_records"][0]["source_metering_point_id"] = "foreign-point"
    elif fault == "precision":
        delivery["normalized_records"][0]["source_quantities"]["grid_kwh"] = "0.6671"
    else:
        delivery["normalized_records"][0]["source_quantities"]["community_kwh"] = (
            "0.334"
        )

    with pytest.raises(billing_runner.BillingRunError, match=message) as error:
        _run(delivery)

    assert error.value.code == "vnb_allocation_mismatch"
    saved.assert_not_called()


@pytest.mark.parametrize("fault", ["missing_interval", "unconfirmed", "provenance"])
def test_corrected_split_does_not_bypass_raw_frame_validation(accepted_split, fault):
    delivery, snapshot, _, saved = accepted_split
    _raw_production(snapshot, "1.003")
    if fault == "missing_interval":
        snapshot["readings"].pop(0)
    elif fault == "unconfirmed":
        snapshot["points"][0]["member_status"] = "invited"
    else:
        for row in snapshot["readings"]:
            row["source_document_id"] = None

    with pytest.raises(billing_runner.BillingRunError) as error:
        _run(delivery)

    assert error.value.reason is None
    saved.assert_not_called()


def test_direct_e66_loader_still_gates_unbalanced_raw_split(accepted_split):
    delivery, snapshot, _, saved = accepted_split
    _raw_production(snapshot, "1.003")

    with pytest.raises(billing_readings.PeriodDataError) as error:
        billing_readings.load_period_frames(
            COMMUNITY, delivery["period_start"], delivery["period_end"]
        )

    assert any(problem["kind"] == "vnb_imbalance" for problem in error.value.problems)
    saved.assert_not_called()


def test_naive_runner_bounds_are_swiss_local_before_accepted_source_lookup(
    accepted_split,
):
    delivery, snapshot, fetched, _ = accepted_split
    _raw_production(snapshot, "1.003")
    bounds = [
        delivery[key].astimezone(ZoneInfo("Europe/Zurich"))
        for key in ("period_start", "period_end")
    ]

    result = billing_runner.run_billing_period(
        COMMUNITY, *(bound.replace(tzinfo=None) for bound in bounds)
    )

    assert result["status"] == "created"
    fetched.assert_called_once_with(COMMUNITY, *bounds)


@pytest.mark.parametrize("status", ["rejected", "partially_invalid", None])
def test_loader_never_promotes_an_unaccepted_delivery(accepted_split, status):
    delivery, snapshot, _, saved = accepted_split
    _raw_production(snapshot, "1.003")
    delivery["status"] = status

    with pytest.raises(
        billing_readings.PeriodDataError, match="delivery is not accepted"
    ):
        billing_readings.load_period_frames(
            COMMUNITY,
            delivery["period_start"],
            delivery["period_end"],
            calculated_delivery=delivery,
        )

    saved.assert_not_called()
