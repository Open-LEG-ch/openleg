# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end contract for one fail-closed billing-period run."""

import hashlib
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

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
    }
    policy_calls = []
    frame_calls = []
    summary_calls = []
    window_calls = []
    saved = []
    existing = []
    frames = SimpleNamespace(
        production=[{"slot": "prod"}],
        consumption=[{"slot": "cons"}],
        provenance={
            "period_start": START,
            "period_end": END,
            "source_document_ids": ("E66-CONSUMPTION", "E66-PRODUCTION"),
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
        datetime(
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


def test_fingerprint_is_the_sha256_of_the_canonical_payload():
    from billing_runner import _fingerprint

    frames = SimpleNamespace(
        provenance={
            "period_start": START,
            "period_end": END,
            "source_document_ids": ("DOC-B", "DOC-A"),
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
    }
    summary = {"z": 1, "a": 2}
    reconciliation = {"difference_kwh": 0, "production_difference_kwh": 0}
    expected_payload = {
        "community_id": COMMUNITY,
        "period_start": START.isoformat(),
        "period_end": END.isoformat(),
        "source_document_ids": ["DOC-B", "DOC-A"],
        "tariff_id": 7,
        "internal_price_chf_per_kwh": "0.12",
        "grid_fee_chf_per_kwh": "0.08",
        "network_level": "same",
        "distribution_model": "proportional",
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
}


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
        },
        {
            "metering_point_id": "CH002",
            "building_id": "building-a",
            "member_status": "confirmed",
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
