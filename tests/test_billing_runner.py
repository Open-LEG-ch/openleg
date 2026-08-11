# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end contract for one fail-closed billing-period run."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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
