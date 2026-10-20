# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exact certificates prove one reported-total witness, not infeasibility."""

from copy import deepcopy

import pytest

from billing_reconciliation import ReconciliationError, audit_e66
from tests.test_billing_reconciliation import evidence


def source():
    return evidence(
        [
            ("a", "consumption", [("0.200", "0.200", "0.000")]),
            ("b", "consumption", [("1.000", "0.314", "0.686")]),
            ("c", "consumption", [("3.000", "0.486", "2.514")]),
            ("p", "production", [("1.000", "1.000", "0.000")]),
        ]
    )


def test_witness_counts_producer_only_structural_zero_and_versions_its_rule():
    certificate = audit_e66(source(), model="einfach")
    assert certificate["version"] == "vnb-einfach-e66-reported-total-witness/1"
    assert certificate["model"] == "einfach"
    assert certificate["witness_basis"] == "reported_total_channels"
    assert certificate["redistribution_threshold_kwh"] == "0.001"
    assert certificate["intervals"][0] == {
        "measured_at": "2026-10-01T00:00:00+00:00",
        "participant_count": 4,
        "allocated_kwh": "1",
        "redistribution_applied": True,
    }


def test_ordering_cannot_change_the_same_witness_or_digest():
    delivery = source()
    first = audit_e66(delivery, model="einfach")
    delivery["normalized_records"].reverse()
    delivery["source_metadata"]["mappings"].reverse()
    assert audit_e66(delivery, model="einfach") == first


def test_exact_remaining_one_millikwh_does_not_redistribute():
    delivery = evidence(
        [
            ("a", "consumption", [("0.001", "0.001", "0.000")]),
            ("b", "consumption", [("0.010", "0.001", "0.009")]),
            ("p", "production", [("0.003", "0.002", "0.001")]),
        ]
    )
    interval = audit_e66(delivery, model="einfach")["intervals"][0]
    assert interval["allocated_kwh"] == "1/500"
    assert interval["redistribution_applied"] is False


def test_a_feasible_noncenter_delivery_remains_unresolved():
    # True c=[.0011,.010,0], P=.0034 produces [.0011,.0023,0].
    # Every reported channel below rounds correctly, while centers give R=.001.
    delivery = evidence(
        [
            ("a", "consumption", [("0.001", "0.001", "0.000")]),
            ("b", "consumption", [("0.010", "0.002", "0.008")]),
            ("p", "production", [("0.003", "0.003", "0.000")]),
        ]
    )
    with pytest.raises(ReconciliationError, match="^unresolved_vnb_einfach_rounding$"):
        audit_e66(delivery, model="einfach")


def test_producer_credits_share_the_allocated_sum_instead_of_physical_production():
    delivery = evidence(
        [
            ("c", "consumption", [("1.000", "1.000", "0.000")]),
            ("p1", "production", [("1.000", "0.333", "0.667")]),
            ("p2", "production", [("2.000", "0.667", "1.333")]),
        ]
    )
    assert audit_e66(delivery, model="einfach")["intervals"][0]["allocated_kwh"] == "1"


def test_half_open_rounding_cells_include_lower_and_exclude_upper_boundary():
    # The exact consumer local and residual are .0005, which round to .001.
    delivery = evidence(
        [
            ("a", "consumption", [("0.001", "0.001", "0.001")]),
            ("b", "consumption", [("0.001", "0.001", "0.001")]),
            ("a", "production", [("0.001", "0.001", "0.000")]),
        ]
    )
    assert audit_e66(delivery, model="einfach")["outcome"] == "rounding_compatible"
    delivery["normalized_records"][0]["source_quantities"]["community_kwh"] = "0.000"
    delivery["normalized_records"][0]["allocated_kwh"] = 0.0
    with pytest.raises(ReconciliationError, match="^unresolved_vnb_einfach_rounding$"):
        audit_e66(delivery, model="einfach")


def test_balanced_materially_different_allocations_are_refused():
    delivery = source()
    for row, local, grid in (
        (delivery["normalized_records"][1], "0.300", "0.700"),
        (delivery["normalized_records"][2], "0.500", "2.500"),
    ):
        row["source_quantities"].update(community_kwh=local, grid_kwh=grid)
        row["allocated_kwh"] = float(local)
    with pytest.raises(ReconciliationError, match="^unresolved_vnb_einfach_rounding$"):
        audit_e66(delivery, model="einfach")


def test_interval_errors_cannot_cancel_against_another_interval():
    delivery = evidence(
        [
            ("a", "consumption", [("0.200", "0.200", "0.000")] * 2),
            (
                "b",
                "consumption",
                [("1.000", "0.304", "0.696"), ("1.000", "0.324", "0.676")],
            ),
            (
                "c",
                "consumption",
                [("3.000", "0.496", "2.504"), ("3.000", "0.476", "2.524")],
            ),
            ("p", "production", [("1.000", "1.000", "0.000")] * 2),
        ]
    )
    with pytest.raises(ReconciliationError, match="^unresolved_vnb_einfach_rounding$"):
        audit_e66(delivery, model="einfach")


@pytest.mark.parametrize(
    "problem",
    [
        "missing_channel",
        "missing_record",
        "duplicate_point",
        "duplicate_participant",
        "extra_precision",
        "normalization",
        "withdrawn",
    ],
)
def test_incomplete_or_inconsistent_evidence_never_gets_a_certificate(problem):
    delivery = source()
    if problem == "missing_channel":
        delivery["normalized_records"][0]["source_quantities"].pop("grid_kwh")
    elif problem == "missing_record":
        delivery["normalized_records"].pop()
    elif problem == "duplicate_point":
        delivery["normalized_records"].append(
            deepcopy(delivery["normalized_records"][0])
        )
    elif problem == "duplicate_participant":
        duplicate = deepcopy(delivery["normalized_records"][0])
        duplicate["source_metering_point_id"] = "second-point-same-participant"
        delivery["normalized_records"].append(duplicate)
        mapping = deepcopy(delivery["source_metadata"]["mappings"][0])
        mapping["metering_point_id"] = duplicate["source_metering_point_id"]
        delivery["source_metadata"]["mappings"].append(mapping)
    elif problem == "extra_precision":
        delivery["normalized_records"][0]["source_quantities"]["grid_kwh"] = "0.0001"
    elif problem == "normalization":
        delivery["normalized_records"][0]["allocated_kwh"] = 0.199
    else:
        delivery["lifecycle_state"] = "withdrawn"
    with pytest.raises(ReconciliationError):
        audit_e66(delivery, model="einfach")
