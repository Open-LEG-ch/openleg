# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exchange contract and billing seam for VNB-calculated LEG values."""

from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import billing_readings
import vnb_calculated_values as exchange

START = "2026-10-25T00:00:00+02:00"
END = "2026-10-25T04:00:00+01:00"  # 20 intervals across the DST fold


class Repository:
    def __init__(self, territory="dietikon"):
        self.territory = territory
        self.saved = {}

    def get_calculated_values_community(self, community_id):
        if community_id != "leg-1":
            return None
        return {
            "community_id": community_id,
            "territory": self.territory,
            "expected_series": [
                {"participant_id": "building-a", "direction": "consumption"}
            ],
        }

    def find_overlapping_calculated_values(self, territory, community_id, start, end):
        return [
            item
            for item in self.saved.values()
            if item["territory"] == territory
            and item["community_id"] == community_id
            and item["status"] == "accepted"
            and item["period_start"] < end
            and item["period_end"] > start
        ]

    def save_calculated_values_delivery(self, delivery):
        prior = self.saved.get(delivery["fingerprint"])
        if prior:
            return {**prior, "replayed": True}
        result = {**delivery, "id": len(self.saved) + 1, "replayed": False}
        self.saved[delivery["fingerprint"]] = result
        return result

    def get_validated_calculated_values(self, community_id, start, end):
        matches = [
            item
            for item in self.saved.values()
            if item["community_id"] == community_id
            and item["period_start"] == start
            and item["period_end"] == end
            and item["status"] == "accepted"
        ]
        return matches[-1] if matches else None


def delivery(unit="kWh", *, missing=0):
    start = datetime.fromisoformat(START)
    moments = []
    moment = start.astimezone(ZoneInfo("UTC"))
    end = datetime.fromisoformat(END).astimezone(ZoneInfo("UTC"))
    while moment < end:
        moments.append(moment.isoformat())
        moment += timedelta(minutes=15)
    records = [
        {
            "measured_at": value,
            "participant_id": "building-a",
            "direction": "consumption",
            "value": 0.25,
        }
        for value in moments[: -missing or None]
    ]
    return {
        "contract_version": "vnb-calculated-values/1",
        "format_version": "json/1",
        "territory": "dietikon",
        "community_id": "leg-1",
        "period_start": START,
        "period_end": END,
        "timezone": "Europe/Zurich",
        "unit": unit,
        "source": "VNB portal export",
        "vnb_case_id": "case-4711",
        "records": records,
    }


def test_contract_declares_capability_and_supported_formats():
    assert exchange.exchange_capabilities() == {
        "calculated_values": {
            "contract_version": "vnb-calculated-values/1",
            "format_versions": ["json/1"],
            "transports": ["api", "file", "manual"],
            "units": ["kWh", "Wh"],
        }
    }


@pytest.mark.parametrize("transport", ["api", "file", "manual"])
def test_all_transports_normalize_to_the_same_outcome(transport):
    result = exchange.accept_delivery(
        delivery(),
        transport=transport,
        evidence=b"private raw export",
        repository=Repository(),
    )
    assert result["status"] == "accepted"
    assert result["record_count"] == 20
    assert result["normalized_records"][0]["allocated_kwh"] == 0.25
    assert result["evidence_sha256"]
    assert "private raw export" not in repr(result)


def test_wh_is_converted_and_replay_is_idempotent():
    repo = Repository()
    payload = delivery("Wh")
    for row in payload["records"]:
        row["value"] = 250
    first = exchange.accept_delivery(
        payload, transport="api", evidence=b"same", repository=repo
    )
    second = exchange.accept_delivery(
        payload, transport="manual", evidence=b"same", repository=repo
    )
    assert first["normalized_records"][0]["allocated_kwh"] == 0.25
    assert first["fingerprint"] == second["fingerprint"]
    assert second["replayed"] is True
    assert len(repo.saved) == 1


def test_a_wh_delivery_replays_onto_the_equivalent_kwh_delivery():
    repo = Repository()
    kwh_payload = delivery()
    wh_payload = delivery("Wh")
    for row in wh_payload["records"]:
        row["value"] = 250
    first = exchange.accept_delivery(
        kwh_payload, transport="api", evidence=b"first", repository=repo
    )
    second = exchange.accept_delivery(
        wh_payload, transport="file", evidence=b"second", repository=repo
    )
    assert first["status"] == "accepted"
    assert second["normalized_records"] == first["normalized_records"]
    assert second["fingerprint"] == first["fingerprint"]
    assert second["replayed"] is True
    assert len(repo.saved) == 1


def test_record_order_does_not_change_replay_identity():
    repo = Repository()
    payload = delivery()
    first = exchange.accept_delivery(
        payload, transport="api", evidence=b"same", repository=repo
    )
    payload["records"].reverse()
    second = exchange.accept_delivery(
        payload, transport="file", evidence=b"same", repository=repo
    )
    assert second["fingerprint"] == first["fingerprint"]
    assert second["replayed"] is True


def test_period_boundaries_must_be_quarter_hour_aligned():
    payload = delivery()
    payload["period_end"] = "2026-10-25T04:01:00+01:00"
    result = exchange.accept_delivery(
        payload, transport="api", evidence=b"raw", repository=Repository()
    )
    assert result["status"] == "rejected"
    assert result["diagnostics"][0] == {"code": "invalid_period"}


def test_a_different_delivery_cannot_overlap_an_accepted_period():
    repo = Repository()
    exchange.accept_delivery(
        delivery(), transport="api", evidence=b"first", repository=repo
    )
    changed = delivery()
    changed["vnb_case_id"] = "case-4712"
    result = exchange.accept_delivery(
        changed, transport="file", evidence=b"second", repository=repo
    )
    assert result["status"] == "rejected"
    assert result["diagnostics"] == [{"code": "overlapping_period"}]


def test_delivery_must_cover_every_declared_community_series():
    repo = Repository()
    repo.get_calculated_values_community = lambda _community_id: {
        "community_id": "leg-1",
        "territory": "dietikon",
        "expected_series": [
            {"participant_id": "building-a", "direction": "consumption"},
            {"participant_id": "building-b", "direction": "consumption"},
        ],
    }
    result = exchange.accept_delivery(
        delivery(), transport="api", evidence=b"partial", repository=repo
    )
    assert result["status"] == "partially_invalid"
    assert result["diagnostics"] == [{"code": "incomplete_period"}]


@pytest.mark.parametrize("bad_record", [None, {"value": "NaN"}, {"value": "Infinity"}])
def test_malformed_and_non_finite_records_are_safe_rejections(bad_record):
    payload = delivery()
    payload["records"][0] = bad_record
    result = exchange.accept_delivery(
        payload, transport="api", evidence=b"raw", repository=Repository()
    )
    assert result["status"] == "partially_invalid"
    assert result["diagnostics"][0] == {"code": "invalid_interval"}


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda item: item.update(community_id="missing"), "unknown_community"),
        (lambda item: item.update(territory="winterthur"), "tenant_mismatch"),
        (lambda item: item.update(unit="MWh"), "unsupported_unit"),
        (lambda item: item.update(format_version="csv/9"), "unsupported_format"),
        (lambda item: item["records"].pop(), "incomplete_period"),
        (
            lambda item: item["records"].append(deepcopy(item["records"][0])),
            "overlapping_interval",
        ),
    ],
)
def test_invalid_deliveries_are_retained_with_safe_diagnostics(mutate, code):
    payload = delivery()
    mutate(payload)
    repo = Repository()
    result = exchange.accept_delivery(
        payload, transport="file", evidence=b"secret", repository=repo
    )
    assert result["status"] in {"rejected", "partially_invalid"}
    assert result["diagnostics"][0]["code"] == code
    assert "secret" not in repr(result)
    assert len(repo.saved) == 1


def test_billing_reference_uses_only_validated_delivery():
    repo = Repository()
    accepted = exchange.accept_delivery(
        delivery(), transport="api", evidence=b"raw", repository=repo
    )
    reference = exchange.load_billing_reference(
        "leg-1", accepted["period_start"], accepted["period_end"], repository=repo
    )
    assert reference["community_consumption_kwh"] == 5.0
    assert reference["per_participant"]["building-a"]["consumption_kwh"] == 5.0
    assert reference["evidence_fingerprint"] == accepted["fingerprint"]


def test_billing_reference_fails_closed_without_validated_evidence():
    with pytest.raises(exchange.CalculatedValuesError, match="validated VNB evidence"):
        exchange.load_billing_reference(
            "leg-1",
            datetime.fromisoformat(START),
            datetime.fromisoformat(END),
            repository=Repository(),
        )


def test_existing_billing_mismatch_seam_uses_accepted_calculated_evidence():
    import pandas as pd

    index = pd.date_range("2026-01-01", periods=1, freq="15min", tz="UTC")
    frames = billing_readings.PeriodFrames(
        consumption=pd.DataFrame({"building-a": [1.0]}, index=index),
        production=pd.DataFrame({"building-a": [1.0]}, index=index),
        participants=("building-a",),
        vnb_reference={},
        provenance={},
    )
    evidence = {
        "status": "accepted",
        "fingerprint": "a" * 64,
        "vnb_case_id": "case-1",
        "source": "VNB",
        "normalized_records": [
            {
                "participant_id": "building-a",
                "direction": "consumption",
                "allocated_kwh": 0.5,
            },
            {
                "participant_id": "building-a",
                "direction": "production",
                "allocated_kwh": 0.5,
            },
        ],
    }
    reconciled = billing_readings.with_calculated_vnb_evidence(frames, evidence)
    result = billing_readings.reconcile_with_vnb(
        reconciled,
        {
            "line_items": [
                {
                    "item_type": "consumer_charge",
                    "participant_id": "building-a",
                    "quantity_kwh": 1.0,
                },
                {
                    "item_type": "producer_credit",
                    "participant_id": "building-a",
                    "quantity_kwh": 1.0,
                },
            ],
            "participants": [{"id": "building-a", "allocated_kwh": 1.0}],
        },
    )
    assert result["difference_kwh"] == 0.5
    assert result["production_difference_kwh"] == 0.5
    assert reconciled.provenance["calculated_values_fingerprint"] == "a" * 64
