# SPDX-License-Identifier: AGPL-3.0-or-later
"""Current-source regressions for PR #758, with storage boundaries replaced."""

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal

import pytest

import billing_approval
import billing_runner
import database
from tests.test_billing_reconciliation import evidence
from tests.test_billing_runner import COMMUNITY, DEFAULT_POLICY


def install_source_runner(monkeypatch, source, *, model="proportional", battery=None):
    source.update(
        community_id=COMMUNITY, vnb_case_id="synthetic-case", source="synthetic-vnb"
    )
    points = [
        {**mapping, "member_status": "confirmed"}
        for mapping in source["source_metadata"]["mappings"]
    ]
    rows = [
        {
            "metering_point_id": row["source_metering_point_id"],
            "direction": row["direction"],
            "measured_at": datetime.fromisoformat(row["measured_at"]),
            "resolution_minutes": 15,
            "source_document_id": "synthetic-document",
            **{key: float(value) for key, value in row["source_quantities"].items()},
        }
        for row in source["normalized_records"]
    ]
    saved = []
    policy = {
        **DEFAULT_POLICY,
        "effective_from": "2026-01-01T00:00:00+00:00",
        "distribution_model": model,
    }
    monkeypatch.setattr(database, "is_db_available", lambda: True)
    monkeypatch.setattr(database, "get_billing_policy", lambda *_: policy)
    monkeypatch.setattr(database, "get_battery", lambda *_: battery)
    monkeypatch.setattr(
        database,
        "get_billable_period_snapshot",
        lambda *_: {"points": points, "readings": rows, "unassigned_point_ids": []},
    )
    monkeypatch.setattr(database, "get_validated_calculated_values", lambda *_: source)
    monkeypatch.setattr(database, "get_billing_period_for_window", lambda *_: None)
    monkeypatch.setattr(
        database,
        "save_billing_period",
        lambda *args, **_kw: saved.append(deepcopy(args[3])) or 42,
    )
    return saved, policy, rows


@pytest.mark.parametrize("direction", ["consumption", "production"])
def test_battery_interval_differences_cannot_cancel_over_the_period(
    monkeypatch, direction
):
    # Equal period totals hide a transfer between daytime solar and battery discharge.
    source = evidence(
        [
            ("a", "consumption", [("1.000", "0.500", "0.500")] * 2),
            ("b", "consumption", [("1.000", "0.500", "0.500")] * 2),
            (
                "solar",
                "production",
                [("1.000", "1.000", "0.000"), ("0.000", "0.000", "0.000")],
            ),
            (
                "battery",
                "production",
                [("0.000", "0.000", "0.000"), ("1.000", "1.000", "0.000")],
            ),
        ]
    )
    if direction == "consumption":
        for row in source["normalized_records"]:
            if row["direction"] == direction:
                local = (
                    ("0.400" if row["participant_id"] == "a" else "0.600")
                    if row["measured_at"]
                    == source["normalized_records"][0]["measured_at"]
                    else ("0.600" if row["participant_id"] == "a" else "0.400")
                )
                row["allocated_kwh"] = float(local)
                row["source_quantities"].update(
                    community_kwh=local, grid_kwh=str(1 - float(local))
                )
    else:
        # Both producers feed in at both times; period credits match the engine.
        for row in source["normalized_records"]:
            if row["direction"] == "consumption":
                row["allocated_kwh"] = 1.0
                row["source_quantities"].update(community_kwh="1.000", grid_kwh="0.000")
        for row in source["normalized_records"]:
            if row["direction"] == direction:
                row["source_quantities"].update(
                    total_kwh="2.000",
                    community_kwh="1.200" if row["allocated_kwh"] else "0.800",
                    grid_kwh="0.800" if row["allocated_kwh"] else "1.200",
                )
                row["allocated_kwh"] = float(row["source_quantities"]["community_kwh"])
    battery = {
        "participant_id": "battery",
        "capacity_kwh": 8,
        "annual_cost_chf": 960,
        "shares": {"a": 40, "b": 40, "solar": 20},
    }
    saved, _, _ = install_source_runner(monkeypatch, source, battery=battery)

    with pytest.raises(billing_runner.BillingRunError) as error:
        billing_runner.run_billing_period(
            COMMUNITY, source["period_start"], source["period_end"]
        )

    assert error.value.code == "vnb_allocation_mismatch"
    assert saved == []


def test_emitted_engine_audit_is_limited_to_each_invoice_owner(monkeypatch):
    source = evidence(
        [
            ("a", "consumption", [("1.000", "0.333", "0.667")]),
            ("b", "consumption", [("2.000", "0.667", "1.333")]),
            ("p", "production", [("1.000", "1.000", "0.000")]),
        ]
    )
    saved, _, _ = install_source_runner(monkeypatch, source)
    billing_runner.run_billing_period(
        COMMUNITY, source["period_start"], source["period_end"]
    )
    draft = {
        **saved[0],
        "id": 42,
        "community_id": COMMUNITY,
        "status": "draft",
        "period_start": source["period_start"],
        "period_end": source["period_end"],
    }
    original = deepcopy(draft)
    snapshots = billing_approval.prepare_invoice_snapshots(
        draft, issue_date=date(2026, 10, 2), calculated_values=source
    )

    for snapshot in snapshots:
        own = snapshot["participant_id"]
        audit = snapshot["provenance_snapshot"]["reconciliation"]["engine_audit"]
        for key, value in original["reconciliation"]["engine_audit"].items():
            expected = (
                ({own: value[own]} if own in value else {})
                if isinstance(value, dict)
                else value
            )
            assert audit[key] == expected
    assert draft == original


# Captured independently by executing the real 43f8826 runner and its modules.
@pytest.mark.parametrize(
    "model,fingerprint",
    [
        (
            "proportional",
            "4c5675e195ea87d7cde2983492d6687e68f9b28867d363485be4d1353c7c81cb",
        ),
        ("einfach", "d65be205c9d0546f2af3f061db1fbd11df5df0d04022f1526a3b2d03d0a74a27"),
    ],
)
def test_main_meter_draft_retry_preserves_hash_and_refuses_changes(
    monkeypatch, model, fingerprint
):
    from tests.test_billing_runner import END, START, _install_billing_fixture

    saved = _install_billing_fixture(
        monkeypatch, policy={**DEFAULT_POLICY, "distribution_model": model}
    )
    monkeypatch.setattr(
        database,
        "get_billing_period_for_window",
        lambda *_: {"id": 42, "status": "draft", "input_fingerprint": fingerprint},
    )
    monkeypatch.setattr(
        database,
        "get_billing_period",
        lambda *_: {"status": "draft", "reconciliation": {}},
    )
    assert billing_runner.run_billing_period(COMMUNITY, START, END) == {
        "status": "already_processed",
        "period_id": 42,
    }
    snapshot = database.get_billable_period_snapshot(COMMUNITY, START, END)
    snapshot["readings"][0]["source_document_id"] = "changed-source"
    with pytest.raises(billing_runner.BillingRunError) as error:
        billing_runner.run_billing_period(COMMUNITY, START, END)
    assert error.value.code == "billing_inputs_changed"
    assert saved == []


@pytest.mark.parametrize(
    "model,fingerprint",
    [
        (
            "proportional",
            "fd2e25c011cc30917d42222d4c7b3e092f090e09e3ac35bfd4f7987d79233244",
        ),
        ("einfach", "6b7bbef74ba3cdb7fa30b011cd804469726f32d220827011de44cec560d92914"),
    ],
)
@pytest.mark.parametrize("changed", ["source", "policy", "readings", "hash"])
def test_main_precision_draft_retry_preserves_hash_and_refuses_changes(
    monkeypatch, model, fingerprint, changed
):
    local = ("0.333", "0.667") if model == "proportional" else ("0.500", "0.500")
    b_total = 2 if model == "proportional" else 1
    source = evidence(
        [
            ("a", "consumption", [("1.000", local[0], str(1 - float(local[0])))]),
            (
                "b",
                "consumption",
                [(f"{b_total}.000", local[1], str(b_total - float(local[1])))],
            ),
            ("p", "production", [("1.000", "1.000", "0.000")]),
        ]
    )
    saved, policy, rows = install_source_runner(monkeypatch, source, model=model)
    existing = {"id": 42, "status": "draft", "input_fingerprint": fingerprint}
    monkeypatch.setattr(database, "get_billing_period_for_window", lambda *_: existing)
    monkeypatch.setattr(
        database,
        "get_billing_period",
        lambda *_: {"status": "draft", "reconciliation": {"precision_policy": {}}},
    )
    assert billing_runner.run_billing_period(
        COMMUNITY, source["period_start"], source["period_end"]
    ) == {"status": "already_processed", "period_id": 42}
    if changed == "source":
        source["fingerprint"] = "c" * 64
    elif changed == "policy":
        policy["internal_price_chf_per_kwh"] = 0.2
    elif changed == "readings":
        rows[0]["total_kwh"] += 1
        rows[0]["grid_kwh"] += 1
    else:
        existing["input_fingerprint"] = "d" * 64
    with pytest.raises(billing_runner.BillingRunError):
        billing_runner.run_billing_period(
            COMMUNITY, source["period_start"], source["period_end"]
        )
    assert saved == []


@pytest.mark.parametrize(
    "format_version,accepted", [("json/1", True), ("e66/1", False)]
)
def test_only_json_quantities_can_fall_back_to_allocated_kwh(format_version, accepted):
    from types import SimpleNamespace

    import pandas as pd

    import billing_readings

    index = pd.date_range("2026-10-01", periods=1, tz="UTC")
    frames = SimpleNamespace(consumption=pd.DataFrame(index=index), participants=("a",))
    source = {
        "format_version": format_version,
        "normalized_records": [
            {
                "participant_id": "a",
                "measured_at": index[0].isoformat(),
                "direction": "consumption",
                "allocated_kwh": 0.5,
            }
        ],
    }
    if accepted:
        consumption, production = billing_readings.calculated_quantity_frames(
            frames, source
        )
        assert float(consumption.at[index[0], "a"]) == 0.5
        assert float(production.at[index[0], "a"]) == 0
    else:
        with pytest.raises(billing_readings.PeriodDataError):
            billing_readings.calculated_quantity_frames(frames, source)


@pytest.mark.parametrize("model", ["proportional", "einfach"])
@pytest.mark.parametrize(
    "tamper",
    [
        None,
        "source_identity",
        "source_quantity",
        "certificate",
        "independent_audit",
        "missing_old_field",
        "engine_audit",
        "vnb_delivered_production_kwh",
        "vnb_imbalance_kwh",
        "vnb_imbalance_ratio",
    ],
)
def test_main_precision_draft_approval_validates_legacy_evidence(
    monkeypatch, model, tamper
):
    local = ("0.333", "0.667") if model == "proportional" else ("0.500", "0.500")
    b_total = 2 if model == "proportional" else 1
    source = evidence(
        [
            ("a", "consumption", [("1.000", local[0], str(1 - float(local[0])))]),
            (
                "b",
                "consumption",
                [(f"{b_total}.000", local[1], str(b_total - float(local[1])))],
            ),
            ("p", "production", [("1.000", "1.000", "0.000")]),
        ]
    )
    saved, _, _ = install_source_runner(monkeypatch, source, model=model)
    billing_runner.run_billing_period(
        COMMUNITY, source["period_start"], source["period_end"]
    )
    draft = {
        **saved[0],
        "id": 42,
        "community_id": COMMUNITY,
        "status": "draft",
        "period_start": source["period_start"],
        "period_end": source["period_end"],
    }
    reconciliation = draft["reconciliation"]
    # These are precisely the fields absent from the 43f8826 runner's payload.
    for key in (
        "engine_audit",
        "vnb_delivered_production_kwh",
        "vnb_imbalance_kwh",
        "vnb_imbalance_ratio",
    ):
        reconciliation.pop(key)
    if tamper == "source_identity":
        source["fingerprint"] = "c" * 64
    elif tamper == "source_quantity":
        # A self-balanced billed transfer still must match the accepted source.
        for item in draft["line_items"]:
            if (
                item["participant_id"] in {"a", "b"}
                and item["item_type"] == "consumer_charge"
            ):
                item["quantity_kwh"] = Decimal(item["quantity_kwh"]) + (
                    Decimal("0.001")
                    if item["participant_id"] == "a"
                    else -Decimal("0.001")
                )
                item["amount_chf"] = round(item["quantity_kwh"] * Decimal("0.12"), 6)
    elif tamper == "certificate":
        reconciliation["precision_policy"]["certificate_sha256"] = "d" * 64
    elif tamper == "independent_audit":
        reconciliation["per_participant"]["a"]["engine_kwh"] += 0.001
    elif tamper == "missing_old_field":
        reconciliation.pop("difference_pct")
    elif tamper is not None:
        # A present new audit field is evidence, including an explicit null value.
        reconciliation[tamper] = {} if tamper == "engine_audit" else None

    if tamper is None:
        snapshots = billing_approval.prepare_invoice_snapshots(
            draft, issue_date=date(2026, 10, 2), calculated_values=source
        )
        assert {row["participant_id"] for row in snapshots} == {"a", "b", "p"}
    else:
        with pytest.raises(billing_approval.BillingApprovalError, match="VNB-Beleg"):
            billing_approval.prepare_invoice_snapshots(
                draft, issue_date=date(2026, 10, 2), calculated_values=source
            )


@pytest.mark.parametrize("model", ["proportional", "einfach"])
def test_legacy_source_validation_still_requires_exact_energy_balance(
    monkeypatch, model
):
    import billing_reconciliation

    source = evidence(
        [
            ("a", "consumption", [("1.000", "0.500", "0.500")]),
            ("b", "consumption", [("1.000", "0.500", "0.500")]),
            ("p", "production", [("1.000", "1.000", "0.000")]),
        ]
    )
    saved, _, _ = install_source_runner(monkeypatch, source, model=model)
    billing_runner.run_billing_period(
        COMMUNITY, source["period_start"], source["period_end"]
    )
    draft = {
        **saved[0],
        "community_id": COMMUNITY,
        "period_start": source["period_start"],
        "period_end": source["period_end"],
    }
    for key in (
        "engine_audit",
        "vnb_delivered_production_kwh",
        "vnb_imbalance_kwh",
        "vnb_imbalance_ratio",
    ):
        draft["reconciliation"].pop(key)
    producer = next(
        row for row in source["normalized_records"] if row["direction"] == "production"
    )
    producer["allocated_kwh"] = 0.999
    producer["source_quantities"].update(community_kwh="0.999", grid_kwh="0.001")
    # Even without a precision certificate, matching each source quantity
    # cannot authorize unequal totals on a legacy draft.
    with pytest.raises(
        billing_reconciliation.ReconciliationError, match="source_quantity_mismatch"
    ):
        billing_reconciliation.validate_billed_source(
            draft,
            source,
            {"a": Decimal("0.5"), "b": Decimal("0.5"), "p": Decimal(0)},
            {"a": Decimal(0), "b": Decimal(0), "p": Decimal("0.999")},
            require_certificate=False,
        )
