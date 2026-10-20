# SPDX-License-Identifier: AGPL-3.0-or-later
"""Check whether rounded source channels can share one proportional model."""

from datetime import UTC, datetime, timedelta
from decimal import localcontext

import pytest


def evidence(series):
    start = datetime(2026, 10, 1, tzinfo=UTC)
    records = []
    mappings = []
    for participant, direction, intervals in series:
        point = f"synthetic-{participant}-{direction}"
        mappings.append(
            {
                "metering_point_id": point,
                "building_id": participant,
                "expected_directions": [direction],
            }
        )
        for index, (total, local, residual) in enumerate(intervals):
            records.append(
                {
                    "participant_id": participant,
                    "direction": direction,
                    "source_metering_point_id": point,
                    "measured_at": (start + timedelta(minutes=15 * index)).isoformat(),
                    "allocated_kwh": float(local),
                    "source_quantities": {
                        "total_kwh": total,
                        "community_kwh": local,
                        "grid_kwh": residual,
                    },
                }
            )
    count = len(series[0][2])
    return {
        "status": "accepted",
        "format_version": "e66/1",
        "fingerprint": "a" * 64,
        "evidence_sha256": "b" * 64,
        "period_start": start,
        "period_end": start + timedelta(minutes=15 * count),
        "normalized_records": records,
        "source_metadata": {"precision": "0.001 kWh", "mappings": mappings},
    }


def test_three_decimal_consumer_allocations_have_a_joint_rounding_explanation():
    from billing_reconciliation import audit_e66

    source = evidence(
        [
            ("a", "consumption", [("1.000", "0.333", "0.667")]),
            ("b", "consumption", [("2.000", "0.667", "1.333")]),
            ("p", "production", [("1.000", "1.000", "0.000")]),
        ]
    )
    result = audit_e66(source, model="proportional")
    assert result["outcome"] == "rounding_compatible"
    assert result["interval_count"] == 1
    assert result["intervals"][0]["limiting_side"] == "production"
    assert result["source_fingerprint"] == source["fingerprint"]


def test_three_decimal_producer_credits_have_the_same_protection():
    from billing_reconciliation import audit_e66

    source = evidence(
        [
            ("c", "consumption", [("1.000", "1.000", "0.000")]),
            ("p1", "production", [("1.000", "0.333", "0.667")]),
            ("p2", "production", [("2.000", "0.667", "1.333")]),
        ]
    )
    result = audit_e66(source, model="proportional")
    assert result["outcome"] == "rounding_compatible"
    assert result["intervals"][0]["limiting_side"] == "consumption"


def test_balanced_totals_do_not_explain_a_materially_different_model():
    from billing_reconciliation import ReconciliationError, audit_e66

    source = evidence(
        [
            ("a", "consumption", [("1.000", "0.300", "0.700")]),
            ("b", "consumption", [("2.000", "0.700", "1.300")]),
            ("p", "production", [("1.000", "1.000", "0.000")]),
        ]
    )
    with pytest.raises(ReconciliationError, match="allocation_disagreement"):
        audit_e66(source, model="proportional")


def test_cancelling_interval_errors_cannot_hide_in_the_period_total():
    from billing_reconciliation import ReconciliationError, audit_e66

    source = evidence(
        [
            (
                "a",
                "consumption",
                [("1.000", "0.323", "0.677"), ("1.000", "0.343", "0.657")],
            ),
            (
                "b",
                "consumption",
                [("2.000", "0.677", "1.323"), ("2.000", "0.657", "1.343")],
            ),
            ("p", "production", [("1.000", "1.000", "0.000")] * 2),
        ]
    )
    with pytest.raises(ReconciliationError, match="allocation_disagreement"):
        audit_e66(source, model="proportional")


def test_zero_production_is_compatible_with_real_zero_local_channels():
    from billing_reconciliation import audit_e66

    source = evidence(
        [
            ("c", "consumption", [("1.000", "0.000", "1.000")]),
            ("p", "production", [("0.000", "0.000", "0.000")]),
        ]
    )
    assert audit_e66(source, model="proportional")["outcome"] == "rounding_compatible"


def test_touching_rounding_boundaries_do_not_prove_a_possible_allocation():
    from billing_reconciliation import ReconciliationError, audit_e66

    source = evidence(
        [
            ("a", "consumption", [("1.000", "0.334", "0.666")]),
            ("b", "consumption", [("2.000", "0.666", "1.334")]),
            ("p", "production", [("1.000", "1.000", "0.000")]),
        ]
    )
    with pytest.raises(ReconciliationError, match="allocation_disagreement"):
        audit_e66(source, model="proportional")


def test_zero_rounded_channels_can_still_contain_positive_true_quantities():
    from billing_reconciliation import audit_e66

    source = evidence(
        [
            ("c", "consumption", [("0.001", "0.000", "0.000")]),
            ("p", "production", [("0.000", "0.000", "0.000")]),
        ]
    )
    result = audit_e66(source, model="proportional")
    assert result["outcome"] == "rounding_compatible"
    assert result["intervals"][0]["ratio"] not in {"0", "1"}


@pytest.mark.parametrize(
    "problem",
    [
        "withdrawn",
        "missing_interval",
        "missing_series",
        "changed_normalization",
        "extra_precision",
        "duplicate",
    ],
)
def test_incomplete_or_inconsistent_source_cannot_gain_a_rounding_certificate(problem):
    from billing_reconciliation import ReconciliationError, audit_e66

    source = evidence(
        [
            ("c", "consumption", [("1.000", "0.500", "0.500")] * 2),
            ("p", "production", [("0.500", "0.500", "0.000")] * 2),
        ]
    )
    if problem == "withdrawn":
        source["lifecycle_state"] = "withdrawn"
    elif problem == "missing_interval":
        source["normalized_records"] = source["normalized_records"][::2]
    elif problem == "missing_series":
        source["normalized_records"].pop()
    elif problem == "changed_normalization":
        source["normalized_records"][0]["allocated_kwh"] = 0.999
    elif problem == "extra_precision":
        source["normalized_records"][0]["source_quantities"]["grid_kwh"] = "0.5001"
    elif problem == "duplicate":
        source["normalized_records"].append(source["normalized_records"][0])
    with pytest.raises(ReconciliationError):
        audit_e66(source, model="proportional")


def test_source_record_order_does_not_change_the_certificate():
    from billing_reconciliation import audit_e66

    source = evidence(
        [
            ("c", "consumption", [("1.000", "0.500", "0.500")]),
            ("p", "production", [("0.500", "0.500", "0.000")]),
        ]
    )
    first = audit_e66(source, model="proportional")
    source["normalized_records"].reverse()
    source["source_metadata"]["mappings"].reverse()
    assert audit_e66(source, model="proportional") == first


@pytest.mark.parametrize("precision", [2, 28, 50])
def test_excess_source_digits_cannot_disappear_in_the_decimal_context(precision):
    from billing_reconciliation import ReconciliationError, audit_e66

    source = evidence(
        [
            (
                "c",
                "consumption",
                [("1.00000000000000000000000000001", "0.500", "0.500")],
            ),
            ("p", "production", [("0.500", "0.500", "0.000")]),
        ]
    )
    with localcontext() as context:
        context.prec = precision
        with pytest.raises(ReconciliationError):
            audit_e66(source, model="proportional")
