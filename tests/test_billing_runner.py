# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end contract for one fail-closed billing-period run."""

import hashlib
import json
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

    policy = {
        "tariff_id": 7,
        "internal_price_chf_per_kwh": 0.12,
        "grid_fee_chf_per_kwh": 0.08,
        "network_level": "same",
        "distribution_model": "proportional",
        "vat_mode": "none",
        "vat_rate_pct": 0,
        "payment_days": 30,
        "invoice_prefix": "LEG-2026",
        "delivery_method": "email",
    }
    policy_calls = []
    frame_calls = []
    summary_calls = []
    window_calls = []
    saved = []
    existing = []
    index = pd.date_range(START, periods=3, freq="15min")
    frames = SimpleNamespace(
        production=pd.DataFrame({"CH002": [0.5, 0.5, 0.5]}, index=index),
        consumption=pd.DataFrame({"CH001": [1.0, 1.0, 1.0]}, index=index),
        participants=("CH001", "CH002"),
        provenance={
            "period_start": START,
            "period_end": END,
            "source_document_ids": ("E66-CONSUMPTION", "E66-PRODUCTION"),
            "interval_count": 3,
            "resolution_minutes": 15,
            "timezone": "Europe/Zurich",
        },
        vnb_reference={"community_kwh": 1.5},
    )
    summary_result = {"participant_count": 2}
    reconciliation_result = {
        "difference_kwh": 0,
        "production_difference_kwh": 0,
        "per_participant": {
            "CH001": {"difference_kwh": 0},
            "CH002": {"difference_kwh": 0},
        },
        "production_per_participant": {
            "CH002": {"difference_kwh": 0},
        },
    }

    monkeypatch.setattr(
        database,
        "get_billing_policy",
        lambda community, period_start, period_end: (
            policy_calls.append((community, period_start, period_end)) or policy
        ),
        raising=False,
    )
    monkeypatch.setattr(
        database,
        "get_billing_period_for_window",
        lambda community, period_start, period_end: (
            window_calls.append((community, period_start, period_end))
            or (existing[0] if existing else None)
        ),
        raising=False,
    )
    monkeypatch.setattr(
        "billing_readings.load_period_frames",
        lambda community, period_start, period_end: (
            frame_calls.append((community, period_start, period_end)) or frames
        ),
    )
    monkeypatch.setattr(
        "billing_readings.reconcile_with_vnb",
        lambda actual_frames, summary: reconciliation_result,
    )
    monkeypatch.setattr(
        "billing_engine.generate_billing_summary",
        lambda production, consumption, **kwargs: (
            summary_calls.append((production, consumption, kwargs))
            or dict(summary_result)
        ),
    )

    def save(community_id, period_start, period_end, summary):
        saved.append((community_id, period_start, period_end, summary))
        return 42

    monkeypatch.setattr(database, "save_billing_period", save)

    created = run_billing_period(COMMUNITY, START, END)

    assert created == {"status": "created", "period_id": 42}
    assert policy_calls == [(COMMUNITY, START, END)]
    assert frame_calls == [(COMMUNITY, START, END)]
    assert window_calls == [(COMMUNITY, START, END)]
    assert summary_calls == [
        (
            frames.production,
            frames.consumption,
            {
                "grid_fee_per_kwh": policy["grid_fee_chf_per_kwh"],
                "internal_price_per_kwh": policy["internal_price_chf_per_kwh"],
                "network_level": policy["network_level"],
                "distribution_model": policy["distribution_model"],
            },
        )
    ]
    assert len(saved) == 1
    assert saved[0][:3] == (COMMUNITY, START, END)
    summary = saved[0][3]
    assert summary["input_fingerprint"]
    assert summary["source_document_ids"] == [
        "E66-CONSUMPTION",
        "E66-PRODUCTION",
    ]
    assert summary["reconciliation"] == reconciliation_result
    assert summary["timezone"] == "Europe/Zurich"

    existing.append({"id": 42, "input_fingerprint": summary["input_fingerprint"]})
    retried = run_billing_period(COMMUNITY, START, END)

    assert retried == {"status": "already_processed", "period_id": 42}
    assert len(saved) == 1

    summary_result["participant_count"] = 99
    with pytest.raises(BillingRunError) as changed_summary:
        run_billing_period(COMMUNITY, START, END)
    assert (
        str(changed_summary.value) == "Billing period inputs changed after processing"
    )

    summary_result["participant_count"] = 2
    reconciliation_result["audit_note"] = "changed"
    with pytest.raises(BillingRunError) as changed_reconciliation_fingerprint:
        run_billing_period(COMMUNITY, START, END)
    assert (
        str(changed_reconciliation_fingerprint.value)
        == "Billing period inputs changed after processing"
    )

    reconciliation_result.pop("audit_note")
    reconciliation_result["difference_kwh"] = 1
    with pytest.raises(BillingRunError) as changed_reconciliation_guard:
        run_billing_period(COMMUNITY, START, END)
    assert (
        str(changed_reconciliation_guard.value)
        == "OpenLEG allocation does not match the VNB allocation"
    )

    reconciliation_result["difference_kwh"] = 0
    frames.vnb_reference = {"community_kwh": 9.9}
    with pytest.raises(BillingRunError) as changed:
        run_billing_period(COMMUNITY, START, END)
    assert str(changed.value) == "Billing period inputs changed after processing"

    existing.clear()
    frames.provenance["source_document_ids"] = ()
    with pytest.raises(BillingRunError) as missing_provenance:
        run_billing_period(COMMUNITY, START, END)
    assert str(missing_provenance.value) == "Billing readings have no import provenance"


def test_previous_complete_month_uses_zurich_calendar_boundaries():
    from billing_runner import previous_complete_month

    start, end = previous_complete_month(
        now=datetime(
            2026,
            11,
            15,
            14,
            37,
            8,
            654321,
            tzinfo=ZoneInfo("Europe/Zurich"),
        )
    )

    assert start == datetime(2026, 10, 1, tzinfo=ZoneInfo("Europe/Zurich"))
    assert end == datetime(2026, 11, 1, tzinfo=ZoneInfo("Europe/Zurich"))
    assert start.utcoffset() != end.utcoffset()


def test_previous_complete_month_asks_datetime_for_zurich_now(monkeypatch):
    import billing_runner

    class _FakeDatetime:
        @staticmethod
        def now(tz):
            assert tz == ZoneInfo("Europe/Zurich")
            return datetime(2026, 3, 15, 9, 1, tzinfo=tz)

    monkeypatch.setattr(billing_runner, "datetime", _FakeDatetime)

    start, end = billing_runner.previous_complete_month()

    assert start == datetime(2026, 2, 1, tzinfo=ZoneInfo("Europe/Zurich"))
    assert end == datetime(2026, 3, 1, tzinfo=ZoneInfo("Europe/Zurich"))


def test_previous_complete_month_starts_from_a_first_of_month_now():
    from billing_runner import previous_complete_month

    start, end = previous_complete_month(
        now=datetime(2026, 7, 1, 0, 0, 0, tzinfo=ZoneInfo("Europe/Zurich"))
    )

    assert start == datetime(2026, 6, 1, tzinfo=ZoneInfo("Europe/Zurich"))
    assert end == datetime(2026, 7, 1, tzinfo=ZoneInfo("Europe/Zurich"))


def test_previous_complete_month_rolls_january_back_to_december():
    from billing_runner import previous_complete_month

    start, end = previous_complete_month(
        now=datetime(2027, 1, 15, 6, 30, tzinfo=ZoneInfo("Europe/Zurich"))
    )

    assert start == datetime(2026, 12, 1, tzinfo=ZoneInfo("Europe/Zurich"))
    assert end == datetime(2027, 1, 1, tzinfo=ZoneInfo("Europe/Zurich"))


def test_fingerprint_is_the_sha256_of_the_canonical_payload():
    from billing_runner import _fingerprint

    index = pd.date_range(START, periods=1, freq="15min")
    frames = SimpleNamespace(
        production=pd.DataFrame({"CH002": [0.5]}, index=index),
        consumption=pd.DataFrame({"CH001": [1.0]}, index=index),
        participants=("CH001", "CH002"),
        provenance={
            "period_start": START,
            "period_end": END,
            "source_document_ids": ("DOC-B", "DOC-A"),
            "interval_count": 1,
            "resolution_minutes": 15,
            "timezone": "Europe/Zurich",
        },
        vnb_reference={"vnb_total_kwh": 3.0},
    )
    policy = {
        "community_id": COMMUNITY,
        "tariff_id": 7,
        "internal_price_chf_per_kwh": 0.12,
        "grid_fee_chf_per_kwh": 0.08,
        "network_level": "same",
        "distribution_model": "proportional",
        "vat_mode": "none",
        "vat_rate_pct": 0,
        "payment_days": 30,
        "invoice_prefix": "LEG-2026",
        "delivery_method": "email",
    }
    summary = {"z": 1, "a": 2}
    reconciliation = {"difference_kwh": 0, "production_difference_kwh": 0}
    expected_payload = {
        "community_id": COMMUNITY,
        "period_start": START.isoformat(),
        "period_end": END.isoformat(),
        "source_document_ids": ["DOC-B", "DOC-A"],
        "interval_count": 1,
        "resolution_minutes": 15,
        "timezone": "Europe/Zurich",
        "production": {
            "index": [START.isoformat()],
            "columns": ["CH002"],
            "values": [[0.5]],
        },
        "consumption": {
            "index": [START.isoformat()],
            "columns": ["CH001"],
            "values": [[1.0]],
        },
        "participants": ["CH001", "CH002"],
        "tariff_id": 7,
        "internal_price_chf_per_kwh": "0.12",
        "grid_fee_chf_per_kwh": "0.08",
        "network_level": "same",
        "distribution_model": "proportional",
        "vat_mode": "none",
        "vat_rate_pct": "0",
        "payment_days": 30,
        "invoice_prefix": "LEG-2026",
        "delivery_method": "email",
        "vnb_reference": {"vnb_total_kwh": 3.0},
        "summary": {"z": 1, "a": 2},
        "reconciliation": {"difference_kwh": 0, "production_difference_kwh": 0},
    }
    expected = hashlib.sha256(
        json.dumps(expected_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    assert _fingerprint(frames, policy, summary, reconciliation) == expected


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
    "vat_mode": "none",
    "vat_rate_pct": 0,
    "payment_days": 30,
    "invoice_prefix": "LEG-2026",
    "delivery_method": "email",
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
        "4f16b38457c142869b04f600a964263827848e553f5938112d0361acf30dad96"
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
        database,
        "get_billable_period_snapshot",
        lambda _community, _start, _end: {
            "points": points,
            "readings": readings,
            "unassigned_point_ids": [],
        },
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
    original_snapshot = database.get_billable_period_snapshot
    monkeypatch.setattr(
        database,
        "get_billable_period_snapshot",
        lambda community, start, end: {
            **original_snapshot(community, start, end),
            "unassigned_point_ids": [point_id],
        },
    )

    with pytest.raises(BillingRunError, match=point_id):
        run_billing_period(COMMUNITY, START, END)

    assert saved == [], "an unassigned point must block the draft"


def test_a_missing_tariff_refuses_to_persist(monkeypatch):
    from billing_runner import BillingRunError, run_billing_period

    saved = _install_billing_fixture(monkeypatch, policy=None)

    with pytest.raises(BillingRunError) as exc:
        run_billing_period(COMMUNITY, START, END)
    assert str(exc.value) == "No effective billing tariff configured"

    assert saved == []


def test_an_incomplete_tariff_surfaces_as_a_billing_run_error(monkeypatch):
    """The cron caller sees BillingRunError, never a raw KeyError from a dict."""
    from billing_runner import BillingRunError, run_billing_period

    incomplete = {key: value for key, value in DEFAULT_POLICY.items()}
    del incomplete["grid_fee_chf_per_kwh"]
    saved = _install_billing_fixture(monkeypatch, policy=incomplete)

    with pytest.raises(BillingRunError) as exc:
        run_billing_period(COMMUNITY, START, END)
    assert str(exc.value) == "'grid_fee_chf_per_kwh'"

    assert saved == []


def test_runner_persists_the_complete_effective_policy_snapshot(monkeypatch):
    """Approval must never reconstruct historic choices from mutable tables."""
    import billing_approval
    from billing_runner import run_billing_period

    policy = {**DEFAULT_POLICY, "effective_from": START}
    saved = _install_billing_fixture(monkeypatch, policy=policy)

    run_billing_period(COMMUNITY, START, END)

    assert len(saved) == 1
    community_id, period_start, period_end, summary = saved[0]
    snapshot = summary["billing_policy_snapshot"]
    assert snapshot == {**policy, "community_id": COMMUNITY}

    period = {
        "id": 42,
        "community_id": community_id,
        "status": "draft",
        "period_start": period_start,
        "period_end": period_end,
        "input_fingerprint": summary["input_fingerprint"],
        "source_document_ids": summary["source_document_ids"],
        "reconciliation": summary["reconciliation"],
        "billing_policy_snapshot": snapshot,
        "line_items": summary["line_items"],
    }
    snapshots = billing_approval.prepare_invoice_snapshots(period)
    assert snapshots
    assert any(s["participant_id"] == "building-a" for s in snapshots)


@pytest.mark.parametrize(
    ("reconciliation", "expected_message"),
    [
        (
            {
                "difference_kwh": 1,
                "production_difference_kwh": 0,
                "per_participant": {"CH001": {"difference_kwh": 0}},
                "production_per_participant": {"CH002": {"difference_kwh": 0}},
            },
            "OpenLEG allocation does not match the VNB allocation",
        ),
        (
            {
                "difference_kwh": 0,
                "production_difference_kwh": 1,
                "per_participant": {"CH001": {"difference_kwh": 0}},
                "production_per_participant": {"CH002": {"difference_kwh": 0}},
            },
            "OpenLEG allocation does not match the VNB allocation",
        ),
        (
            {
                "difference_kwh": 0,
                "production_difference_kwh": 0,
                "per_participant": {"CH001": {"difference_kwh": 1}},
                "production_per_participant": {"CH002": {"difference_kwh": 0}},
            },
            "OpenLEG allocation does not match the VNB allocation",
        ),
        (
            {
                "difference_kwh": 0,
                "production_difference_kwh": 0,
                "per_participant": {"CH001": {"difference_kwh": 0}},
                "production_per_participant": {"CH002": {"difference_kwh": 1}},
            },
            "OpenLEG allocation does not match the VNB allocation",
        ),
    ],
)
def test_every_non_zero_reconciliation_gap_blocks_persistence(
    monkeypatch, reconciliation, expected_message
):
    from billing_runner import BillingRunError, run_billing_period

    frames = SimpleNamespace(
        production=[{"slot": "prod"}],
        consumption=[{"slot": "cons"}],
        provenance={
            "period_start": START,
            "period_end": END,
            "source_document_ids": ("DOC-1",),
            "timezone": "Europe/Zurich",
        },
        vnb_reference={"community_kwh": 1.5},
    )
    saved = []

    monkeypatch.setattr(
        database,
        "get_billing_policy",
        lambda community, period_start, period_end: DEFAULT_POLICY,
        raising=False,
    )
    monkeypatch.setattr(
        "billing_readings.load_period_frames",
        lambda community, period_start, period_end: frames,
    )
    monkeypatch.setattr(
        "billing_engine.generate_billing_summary",
        lambda *args, **kwargs: {"participant_count": 2},
    )
    monkeypatch.setattr(
        "billing_readings.reconcile_with_vnb",
        lambda actual_frames, summary: reconciliation,
    )
    monkeypatch.setattr(
        database,
        "get_billing_period_for_window",
        lambda *args: None,
        raising=False,
    )
    monkeypatch.setattr(
        database,
        "save_billing_period",
        lambda *args: saved.append(args) or 42,
    )

    with pytest.raises(BillingRunError) as exc:
        run_billing_period(COMMUNITY, START, END)

    assert str(exc.value) == expected_message
    assert saved == []
