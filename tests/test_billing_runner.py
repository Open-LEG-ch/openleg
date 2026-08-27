# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end contract for one fail-closed billing-period run."""

from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import database

COMMUNITY = "community-a"
START = datetime(2026, 1, 1, tzinfo=ZoneInfo("Europe/Zurich"))
END = START + timedelta(minutes=45)


def test_run_billing_period_persists_once_and_retries_as_a_noop(monkeypatch):
    from billing_runner import BillingRunError, run_billing_period

    points = [
        {
            "metering_point_id": "CH001",
            "building_id": "building-a",
            "member_status": "confirmed",
            "expected_directions": ["consumption"],
        },
        {
            "metering_point_id": "CH002",
            "building_id": "building-a",
            "member_status": "confirmed",
            "expected_directions": ["production"],
        },
    ]
    readings = []
    for offset in range(3):
        measured_at = START + timedelta(minutes=15 * offset)
        readings.extend(
            (
                {
                    "metering_point_id": "CH001",
                    "direction": "consumption",
                    "measured_at": measured_at,
                    "resolution_minutes": 15,
                    "total_kwh": 1.0,
                    "grid_kwh": 0.5,
                    "community_kwh": 0.5,
                    "source_document_id": "E66-CONSUMPTION",
                },
                {
                    "metering_point_id": "CH002",
                    "direction": "production",
                    "measured_at": measured_at,
                    "resolution_minutes": 15,
                    "total_kwh": 0.5,
                    "grid_kwh": 0.0,
                    "community_kwh": 0.5,
                    "source_document_id": "E66-PRODUCTION",
                },
            )
        )

    policy = {
        "tariff_id": 7,
        "internal_price_chf_per_kwh": 0.12,
        "grid_fee_chf_per_kwh": 0.08,
        "network_level": "same",
        "distribution_model": "proportional",
    }
    saved = []
    existing = []

    monkeypatch.setattr(
        database, "get_community_metering_points", lambda _community: points
    )
    monkeypatch.setattr(
        database,
        "get_unassigned_period_metering_point_ids",
        lambda _community, _start, _end: [],
    )
    monkeypatch.setattr(
        database,
        "get_period_readings",
        lambda _community, _start, _end: readings,
    )
    monkeypatch.setattr(
        database,
        "get_billing_policy",
        lambda _community, _start, _end: policy,
        raising=False,
    )
    monkeypatch.setattr(
        database,
        "get_billing_period_for_window",
        lambda _community, _start, _end: existing[0] if existing else None,
        raising=False,
    )

    def save(community_id, period_start, period_end, summary):
        saved.append((community_id, period_start, period_end, summary))
        return 42

    monkeypatch.setattr(database, "save_billing_period", save)

    created = run_billing_period(COMMUNITY, START, END)

    assert created == {"status": "created", "period_id": 42}
    assert len(saved) == 1
    summary = saved[0][3]
    assert summary["input_fingerprint"]
    assert summary["source_document_ids"] == [
        "E66-CONSUMPTION",
        "E66-PRODUCTION",
    ]
    assert summary["reconciliation"]["difference_kwh"] == 0
    assert summary["reconciliation"]["production_difference_kwh"] == 0

    existing.append({"id": 42, "input_fingerprint": summary["input_fingerprint"]})
    retried = run_billing_period(COMMUNITY, START, END)

    assert retried == {"status": "already_processed", "period_id": 42}
    assert len(saved) == 1

    readings[0]["total_kwh"] = 2.0
    with pytest.raises(BillingRunError, match="inputs changed"):
        run_billing_period(COMMUNITY, START, END)

    existing.clear()
    for reading in readings:
        reading["source_document_id"] = None
    with pytest.raises(BillingRunError, match="provenance"):
        run_billing_period(COMMUNITY, START, END)


def test_previous_complete_month_uses_zurich_calendar_boundaries():
    from billing_runner import previous_complete_month

    start, end = previous_complete_month(
        datetime(2026, 11, 15, tzinfo=ZoneInfo("Europe/Zurich"))
    )

    assert start == datetime(2026, 10, 1, tzinfo=ZoneInfo("Europe/Zurich"))
    assert end == datetime(2026, 11, 1, tzinfo=ZoneInfo("Europe/Zurich"))
    assert start.utcoffset() != end.utcoffset()


# ---------------------------------------------------------------------------
# The guards the module exists for.
#
# Every fixture above is built so the VNB reconciliation agrees and the policy
# is present, so the three fail-closed branches in run_billing_period had never
# been fired by a test.
# ---------------------------------------------------------------------------

DEFAULT_POLICY = {
    "tariff_id": 7,
    "internal_price_chf_per_kwh": 0.12,
    "grid_fee_chf_per_kwh": 0.08,
    "network_level": "same",
    "distribution_model": "proportional",
}


def _fingerprint_case():
    index = pd.date_range(start=START, end=END, inclusive="left", freq="15min")
    return {
        "community_id": COMMUNITY,
        "period_start": START,
        "period_end": END,
        "policy": deepcopy(DEFAULT_POLICY),
        "frames": SimpleNamespace(
            production=pd.DataFrame({"building-a": [0.5, 0.5, 0.5]}, index=index),
            consumption=pd.DataFrame({"building-a": [1.0, 1.0, 1.0]}, index=index),
            participants=("building-a",),
            vnb_reference={
                "community_consumption_kwh": 1.5,
                "community_production_kwh": 1.5,
                "per_participant": {
                    "building-a": {
                        "consumption_kwh": 1.5,
                        "production_kwh": 1.5,
                    }
                },
            },
            provenance={
                "source_document_ids": ("E66-A", "E66-B"),
                "interval_count": 3,
                "resolution_minutes": 15,
                "period_start": START,
                "period_end": END,
                "timezone": "Europe/Zurich",
            },
        ),
        "summary": {
            "total_production_kwh": 1.5,
            "total_allocated_kwh": 1.5,
            "total_surplus_kwh": 0.0,
            "total_network_discount_chf": 0.05,
            "participants": [
                {
                    "id": "building-a",
                    "consumption_kwh": 3.0,
                    "allocated_kwh": 1.5,
                }
            ],
            "line_items": [
                {
                    "participant_id": "building-a",
                    "item_type": "consumer_charge",
                    "quantity_kwh": 1.5,
                    "amount_chf": 0.18,
                }
            ],
        },
        "reconciliation": {
            "vnb_allocated_kwh": 1.5,
            "engine_allocated_kwh": 1.5,
            "difference_kwh": 0.0,
            "difference_pct": 0.0,
            "per_participant": {
                "building-a": {
                    "vnb_kwh": 1.5,
                    "engine_kwh": 1.5,
                    "difference_kwh": 0.0,
                }
            },
            "vnb_production_kwh": 1.5,
            "engine_production_kwh": 1.5,
            "production_difference_kwh": 0.0,
            "production_per_participant": {
                "building-a": {
                    "vnb_kwh": 1.5,
                    "engine_kwh": 1.5,
                    "difference_kwh": 0.0,
                }
            },
        },
    }


def _fingerprint_through_runner(monkeypatch, case):
    import billing_runner

    saved = []
    monkeypatch.setattr(
        database,
        "get_billing_policy",
        lambda _community, _start, _end: deepcopy(case["policy"]),
    )
    monkeypatch.setattr(
        billing_runner.billing_readings,
        "load_period_frames",
        lambda _community, _start, _end: deepcopy(case["frames"]),
    )
    monkeypatch.setattr(
        billing_runner.billing_engine,
        "generate_billing_summary",
        lambda *_args, **_kwargs: deepcopy(case["summary"]),
    )
    monkeypatch.setattr(
        billing_runner.billing_readings,
        "reconcile_with_vnb",
        lambda _frames, _summary: deepcopy(case["reconciliation"]),
    )
    monkeypatch.setattr(
        database,
        "get_billing_period_for_window",
        lambda _community, _start, _end: None,
    )
    monkeypatch.setattr(
        database,
        "save_billing_period",
        lambda *_args: saved.append(_args) or 42,
    )

    result = billing_runner.run_billing_period(
        case["community_id"], case["period_start"], case["period_end"]
    )

    assert result == {"status": "created", "period_id": 42}
    return saved[0][3]["input_fingerprint"]


def _change_fingerprint_input(case, input_name):
    if input_name == "community_id":
        case["community_id"] = "community-b"
    elif input_name == "period_start":
        changed = START - timedelta(minutes=15)
        case["period_start"] = changed
        case["frames"].provenance["period_start"] = changed
    elif input_name == "period_end":
        changed = END + timedelta(minutes=15)
        case["period_end"] = changed
        case["frames"].provenance["period_end"] = changed
    elif input_name == "source_document_ids":
        case["frames"].provenance["source_document_ids"] = ("E66-A", "E66-C")
    elif input_name == "interval_count":
        case["frames"].provenance["interval_count"] = 4
    elif input_name == "resolution_minutes":
        case["frames"].provenance["resolution_minutes"] = 30
    elif input_name == "timezone":
        case["frames"].provenance["timezone"] = "UTC"
    elif input_name == "production_frame_value":
        case["frames"].production.iloc[0, 0] += 0.125
    elif input_name == "consumption_frame_value":
        case["frames"].consumption.iloc[0, 0] += 0.125
    elif input_name == "frame_index":
        changed = case["frames"].production.index + timedelta(minutes=1)
        case["frames"].production.index = changed
        case["frames"].consumption.index = changed
    elif input_name == "participants":
        case["frames"].participants = ("building-b",)
    elif input_name == "vnb_community_total":
        case["frames"].vnb_reference["community_consumption_kwh"] = 1.625
    elif input_name == "vnb_participant_total":
        case["frames"].vnb_reference["per_participant"]["building-a"][
            "consumption_kwh"
        ] = 1.625
    elif input_name == "tariff_id":
        case["policy"]["tariff_id"] = 8
    elif input_name == "internal_price":
        case["policy"]["internal_price_chf_per_kwh"] = 0.13
    elif input_name == "grid_fee":
        case["policy"]["grid_fee_chf_per_kwh"] = 0.09
    elif input_name == "network_level":
        case["policy"]["network_level"] = "cross"
    elif input_name == "distribution_model":
        case["policy"]["distribution_model"] = "einfach"
    elif input_name == "summary_total":
        case["summary"]["total_production_kwh"] = 1.625
    elif input_name == "summary_participant":
        case["summary"]["participants"][0]["consumption_kwh"] = 3.125
    elif input_name == "summary_line_item":
        case["summary"]["line_items"][0]["amount_chf"] = 0.19
    elif input_name == "reconciliation_total":
        case["reconciliation"]["vnb_allocated_kwh"] = 1.625
        case["reconciliation"]["engine_allocated_kwh"] = 1.625
    elif input_name == "reconciliation_participant":
        participant = case["reconciliation"]["per_participant"]["building-a"]
        participant["vnb_kwh"] = 1.625
        participant["engine_kwh"] = 1.625
    elif input_name == "production_reconciliation_participant":
        participant = case["reconciliation"]["production_per_participant"]["building-a"]
        participant["vnb_kwh"] = 1.625
        participant["engine_kwh"] = 1.625
    else:
        raise AssertionError(f"unhandled fingerprint input: {input_name}")


@pytest.mark.parametrize(
    "input_name",
    (
        "community_id",
        "period_start",
        "period_end",
        "source_document_ids",
        "interval_count",
        "resolution_minutes",
        "timezone",
        "production_frame_value",
        "consumption_frame_value",
        "frame_index",
        "participants",
        "vnb_community_total",
        "vnb_participant_total",
        "tariff_id",
        "internal_price",
        "grid_fee",
        "network_level",
        "distribution_model",
        "summary_total",
        "summary_participant",
        "summary_line_item",
        "reconciliation_total",
        "reconciliation_participant",
        "production_reconciliation_participant",
    ),
)
def test_each_billing_input_changes_the_public_run_fingerprint(monkeypatch, input_name):
    baseline = _fingerprint_through_runner(monkeypatch, _fingerprint_case())
    changed_case = _fingerprint_case()
    _change_fingerprint_input(changed_case, input_name)

    changed = _fingerprint_through_runner(monkeypatch, changed_case)

    assert changed != baseline, f"{input_name} is missing from the fingerprint"


def test_equivalent_billing_inputs_keep_a_stable_fingerprint(monkeypatch):
    baseline_case = _fingerprint_case()
    equivalent_case = _fingerprint_case()
    equivalent_case["policy"] = dict(reversed(equivalent_case["policy"].items()))
    equivalent_case["summary"] = dict(reversed(equivalent_case["summary"].items()))
    equivalent_case["reconciliation"] = dict(
        reversed(equivalent_case["reconciliation"].items())
    )

    baseline = _fingerprint_through_runner(monkeypatch, baseline_case)
    equivalent = _fingerprint_through_runner(monkeypatch, equivalent_case)

    assert equivalent == baseline


def test_public_billing_fingerprint_matches_the_contract_vector(monkeypatch):
    fingerprint = _fingerprint_through_runner(monkeypatch, _fingerprint_case())

    assert fingerprint == (
        "d530ebb158743b6ebb0efdb3514c06ac408f3c942df38fecbcb12c90e0dc9d2d"
    )


def _install_billing_fixture(
    monkeypatch, *, policy=DEFAULT_POLICY, consumption_community_kwh=0.5
):
    """Wire one community whose readings the engine and the VNB both describe.

    ``consumption_community_kwh`` is the VNB's own claim about how much of the
    consumption came from the community. Lower it and the VNB disagrees with
    what allocate_energy derives from the same totals.
    """
    points = [
        {
            "metering_point_id": "CH001",
            "building_id": "building-a",
            "member_status": "confirmed",
            "expected_directions": ["consumption"],
        },
        {
            "metering_point_id": "CH002",
            "building_id": "building-a",
            "member_status": "confirmed",
            "expected_directions": ["production"],
        },
    ]
    readings = []
    for offset in range(3):
        measured_at = START + timedelta(minutes=15 * offset)
        readings.extend(
            (
                {
                    "metering_point_id": "CH001",
                    "direction": "consumption",
                    "measured_at": measured_at,
                    "resolution_minutes": 15,
                    "total_kwh": 1.0,
                    "grid_kwh": 1.0 - consumption_community_kwh,
                    "community_kwh": consumption_community_kwh,
                    "source_document_id": "E66-CONSUMPTION",
                },
                {
                    "metering_point_id": "CH002",
                    "direction": "production",
                    "measured_at": measured_at,
                    "resolution_minutes": 15,
                    "total_kwh": 0.5,
                    "grid_kwh": 0.0,
                    "community_kwh": 0.5,
                    "source_document_id": "E66-PRODUCTION",
                },
            )
        )

    saved = []
    monkeypatch.setattr(
        database, "get_community_metering_points", lambda _community: points
    )
    monkeypatch.setattr(
        database,
        "get_unassigned_period_metering_point_ids",
        lambda _community, _start, _end: [],
    )
    monkeypatch.setattr(
        database, "get_period_readings", lambda _community, _start, _end: readings
    )
    monkeypatch.setattr(
        database,
        "get_billing_policy",
        lambda _community, _start, _end: policy,
        raising=False,
    )
    monkeypatch.setattr(
        database,
        "get_billing_period_for_window",
        lambda _community, _start, _end: None,
        raising=False,
    )
    monkeypatch.setattr(
        database,
        "save_billing_period",
        lambda *args: saved.append(args) or 42,
    )
    return saved


def test_a_vnb_allocation_mismatch_refuses_to_persist(monkeypatch):
    """The namesake guard: OpenLEG never bills a split the VNB does not confirm."""
    from billing_runner import BillingRunError, run_billing_period

    saved = _install_billing_fixture(monkeypatch, consumption_community_kwh=0.25)

    with pytest.raises(BillingRunError, match="does not match the VNB allocation"):
        run_billing_period(COMMUNITY, START, END)

    assert saved == [], "a period the VNB contradicts must never reach the database"


def test_an_unassigned_period_point_refuses_to_persist(monkeypatch):
    from billing_runner import BillingRunError, run_billing_period

    saved = _install_billing_fixture(monkeypatch)
    point_id = "CH000000000000000000000000000099"
    monkeypatch.setattr(
        database,
        "get_unassigned_period_metering_point_ids",
        lambda _community, _start, _end: [point_id],
    )

    with pytest.raises(BillingRunError, match=point_id):
        run_billing_period(COMMUNITY, START, END)

    assert saved == [], "an unassigned point must block the draft"


def test_a_missing_tariff_refuses_to_persist(monkeypatch):
    from billing_runner import BillingRunError, run_billing_period

    saved = _install_billing_fixture(monkeypatch, policy=None)

    with pytest.raises(BillingRunError, match="No effective billing tariff"):
        run_billing_period(COMMUNITY, START, END)

    assert saved == []


def test_an_incomplete_tariff_surfaces_as_a_billing_run_error(monkeypatch):
    """The cron caller sees BillingRunError, never a raw KeyError from a dict."""
    from billing_runner import BillingRunError, run_billing_period

    incomplete = {key: value for key, value in DEFAULT_POLICY.items()}
    del incomplete["grid_fee_chf_per_kwh"]
    saved = _install_billing_fixture(monkeypatch, policy=incomplete)

    with pytest.raises(BillingRunError):
        run_billing_period(COMMUNITY, START, END)

    assert saved == []
