# SPDX-License-Identifier: AGPL-3.0-or-later
"""Coverage arithmetic preserves channels, timestamps and decimal precision."""

from datetime import UTC, datetime
from decimal import localcontext

import telemetry_summary

NOW = datetime(2026, 10, 25, 1, 30, tzinfo=UTC)


def installation(owner, **fields):
    sample = {
        "metric": "power",
        "unit": "W",
        "measurement_location": "pv_inverter",
        "direction": "generation",
        "quality": "measured",
        "freshness": "fresh",
        "delivery_status": "recent",
        "value": "0.123456789012",
        "observed_at": NOW.isoformat(),
        **fields,
    }
    return {
        "id": owner,
        "owner_building_id": owner,
        "source_id": owner,
        "device_id": owner,
        "cadence_seconds": 30,
        "samples": [sample],
    }


def test_equal_instants_across_dst_offsets_sum_under_hostile_decimal_context():
    sources = [installation(str(n)) for n in range(3)]
    sources[0]["samples"][0]["observed_at"] = "2026-10-25T02:30:00+01:00"
    with localcontext() as context:
        context.prec = 2
        summary = telemetry_summary.view(sources, NOW, "community")
    assert summary["groups"][0]["value"] == "0.370370367036"
    assert summary["groups"][0]["state"] == "available"


def test_pv_and_grid_export_never_form_a_combined_total():
    sources = [installation(str(n), value="100") for n in range(3)]
    for source in sources:
        source["samples"].append(
            {
                **source["samples"][0],
                "measurement_location": "grid_connection",
                "direction": "export",
                "value": "40",
            }
        )
    groups = telemetry_summary.view(sources, NOW, "community")["groups"]
    assert {
        (g["measurement_location"], g["direction"], g["value"]) for g in groups
    } == {
        ("pv_inverter", "generation", "300"),
        ("grid_connection", "export", "120"),
    }


def test_energy_battery_state_and_fault_flags_do_not_enter_power_sums():
    sources = [
        installation(str(n), metric="energy_counter", unit="kWh") for n in range(3)
    ]
    sources += [
        installation(
            "battery",
            metric="state_of_charge",
            unit="%",
            measurement_location="battery",
            direction="stored",
        ),
        installation(
            "fault",
            metric="device_fault",
            unit="state",
            measurement_location="device",
            direction="status",
        ),
    ]
    summary = telemetry_summary.view(sources, NOW, "community")
    assert summary["groups"] == []
    assert all(not source["samples"] for source in summary["sources"])


def test_partial_coverage_can_sum_three_remaining_nonoverlapping_profiles():
    sources = [installation(str(n), value="100") for n in range(4)]
    sources.append({**installation("0", value="999"), "id": "overlapping-source"})
    group = telemetry_summary.view(sources, NOW, "community")["groups"][0]
    assert group["state"] == "available" and group["value"] == "300"
    assert group["contributing_profiles"] == 3
    assert group["visible_profiles"] == 4
    assert group["excluded"]["overlapping"] == 1
