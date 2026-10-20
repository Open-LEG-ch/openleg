# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keep source, approval and retry gates for certified einfach billing."""

import json
import xml.etree.ElementTree as ET
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

import billing_approval
import billing_engine
import billing_readings
import billing_reconciliation
import billing_runner
from tests import test_billing_einfach_precision_postgres as einfach
from tests import test_e66_evidence_postgres as e66

interest_database = einfach.interest_database
calculated_client = einfach.calculated_client
assignment_request = einfach.assignment_request
configured_e66 = einfach.configured_e66
rounded_delivery = einfach.rounded_delivery
einfach_delivery = einfach.einfach_delivery
pytestmark = pytest.mark.integration


def _window(payload):
    return tuple(
        datetime.fromisoformat(payload[key]).astimezone(ZoneInfo("Europe/Zurich"))
        for key in ("period_start", "period_end")
    )


def _prepare(fixture):
    client, db, payload, source = fixture
    imported = e66.post_e66(client, source)
    assert imported.status_code == 201
    start, end = _window(payload)
    result = billing_runner.run_billing_period(
        payload["community_id"], start, end, prepared_by="member"
    )
    return (
        imported.get_json(),
        result,
        db.get_billing_period(result["period_id"], payload["community_id"]),
    )


def test_einfach_retries_and_issued_snapshots_survive_source_withdrawal(
    einfach_delivery,
):
    client, db, payload, _ = einfach_delivery
    source, result, period = _prepare(einfach_delivery)
    start, end = _window(payload)
    assert billing_runner.run_billing_period(
        payload["community_id"], start, end, prepared_by="member"
    ) == {"status": "already_processed", "period_id": result["period_id"]}
    assert db.get_billing_period(result["period_id"], payload["community_id"]) == period
    invoices = db.approve_billing_period(
        result["period_id"], payload["community_id"], approver_id="producer"
    )
    response = client.post(
        f"/admin/vnb-calculated-values/dietikon/{source['id']}/withdrawals",
        headers={
            "X-Admin-Token": "synthetic-admin",
            "Idempotency-Key": "einfach-issued",
        },
        json={"reason": "Synthetic source correction after issue"},
    )
    assert response.status_code == 201
    assert (
        db.approve_billing_period(
            result["period_id"], payload["community_id"], approver_id="producer"
        )
        == invoices
    )


@pytest.mark.parametrize(
    "forgery", ["certificate", "independent", "quantities", "marker"]
)
def test_einfach_source_or_certificate_forgery_cannot_issue_invoices(
    einfach_delivery, forgery
):
    _, db, payload, _ = einfach_delivery
    _, result, period = _prepare(einfach_delivery)
    reconciliation = deepcopy(period["reconciliation"])
    if forgery == "certificate":
        reconciliation["precision_policy"]["certificate_sha256"] = "0" * 64
    elif forgery == "marker":
        reconciliation.pop("precision_policy")
    elif forgery == "independent":
        for entry in reconciliation["per_participant"].values():
            entry["engine_kwh"] = entry["vnb_kwh"]
            entry["difference_kwh"] = 0
    else:
        for participant, quantity in (("consumer-b", 0.315), ("consumer-c", 0.485)):
            entry = reconciliation["per_participant"][participant]
            entry["vnb_kwh"] = quantity
            entry["difference_kwh"] = round(entry["engine_kwh"] - quantity, 6)
            with db.get_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE billing_line_items SET quantity_kwh=%s, amount_chf=%s * unit_price_chf_per_kwh WHERE billing_period_id=%s AND participant_id=%s AND item_type='consumer_charge'",
                    (quantity, quantity, result["period_id"], participant),
                )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE billing_periods SET reconciliation=%s WHERE id=%s",
            (json.dumps(reconciliation), result["period_id"]),
        )
    with pytest.raises(billing_approval.BillingApprovalError):
        db.approve_billing_period(
            result["period_id"], payload["community_id"], approver_id="producer"
        )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS count FROM invoices")
        assert cur.fetchone()["count"] == 0


def test_einfach_rounding_witness_does_not_supply_an_imbalance_policy(einfach_delivery):
    client, db, payload, source = einfach_delivery
    root = ET.fromstring(source)
    for block in root.findall("MeteringData"):
        if block.find("ConsumptionMeteringPoint") is not None:
            block.find("Observation/Volume").text = {
                "8716867000030": "1.000",
                "2404050010123": "0.333",
                "2404050010124": "0.667",
            }[block.find("Product/ID/VSENationalCode").text]
    assert e66.post_e66(client, ET.tostring(root)).status_code == 201
    start, end = _window(payload)
    delivery = db.get_validated_calculated_values(payload["community_id"], start, end)
    assert (
        billing_reconciliation.audit_e66(delivery, model="einfach")["outcome"]
        == "rounding_compatible"
    )
    with pytest.raises(billing_runner.BillingRunError) as error:
        billing_runner.run_billing_period(payload["community_id"], start, end)
    assert error.value.code == "vnb_allocation_mismatch"
    assert error.value.reason == "vnb_balancing_policy_required"
    assert db.get_billing_period_for_window(payload["community_id"], start, end) is None


def test_einfach_cancelling_interval_errors_cannot_hide_in_exact_monthly_totals(
    einfach_delivery,
):
    client, db, payload, source = einfach_delivery
    root = ET.fromstring(source)
    finish = "2026-09-30T23:45:00Z"
    root.find(".//ReportPeriod/EndDateTime").text = finish
    local_values = {
        "SYNTHETIC-CONSUMER-B": [
            ".304",
            ".324",
            ".314",
            ".314",
            ".314",
            ".315",
            ".315",
        ],
        "SYNTHETIC-CONSUMER-C": [
            ".496",
            ".476",
            ".486",
            ".486",
            ".486",
            ".485",
            ".485",
        ],
    }
    for block in root.findall("MeteringData"):
        block.find("Interval/EndDateTime").text = finish
        code = block.find("Product/ID/VSENationalCode").text
        point = block.find("ConsumptionMeteringPoint/VSENationalID")
        first = block.find("Observation")
        template = ET.tostring(first)
        block.remove(first)
        for index in range(7):
            observation = ET.fromstring(template)
            observation.find("Position/Sequence").text = str(index + 1)
            if (
                point is not None
                and point.text in local_values
                and code != "8716867000030"
            ):
                local = Decimal(local_values[point.text][index])
                total = Decimal(1 if point.text == "SYNTHETIC-CONSUMER-B" else 3)
                quantity = local if code == "2404050010123" else total - local
                observation.find("Volume").text = format(quantity, ".3f")
            block.append(observation)
    assert e66.post_e66(client, ET.tostring(root)).status_code == 201
    start, _ = _window(payload)
    end = datetime.fromisoformat(finish).astimezone(ZoneInfo("Europe/Zurich"))
    delivery = db.get_validated_calculated_values(payload["community_id"], start, end)
    frames = billing_readings.frames_from_calculated_source(delivery)
    independent = billing_engine.generate_billing_summary(
        frames.production,
        frames.consumption,
        0.08,
        0.12,
        "same",
        distribution_model="einfach",
    )
    audit = billing_readings.reconcile_with_vnb(frames, independent)
    assert audit["difference_kwh"] == audit["production_difference_kwh"] == 0
    assert all(
        entry["difference_kwh"] == 0 for entry in audit["per_participant"].values()
    )
    assert all(
        entry["difference_kwh"] == 0
        for entry in audit["production_per_participant"].values()
    )
    with pytest.raises(
        billing_runner.BillingRunError, match="unresolved_vnb_einfach_rounding"
    ):
        billing_runner.run_billing_period(payload["community_id"], start, end)
    assert db.get_billing_period_for_window(payload["community_id"], start, end) is None


def test_einfach_rounding_never_bypasses_existing_battery_exact_gate(einfach_delivery):
    client, db, payload, source = einfach_delivery
    db.save_battery(
        payload["community_id"],
        {
            "participant_id": "producer",
            "capacity_kwh": "100.00",
            "annual_cost_chf": "120.00",
            "shares": {"member": "34", "consumer-b": "33", "consumer-c": "33"},
        },
    )
    assert e66.post_e66(client, source).status_code == 201
    start, end = _window(payload)
    with pytest.raises(billing_runner.BillingRunError) as error:
        billing_runner.run_billing_period(payload["community_id"], start, end)
    assert error.value.code == "vnb_allocation_mismatch"
    assert db.get_billing_period_for_window(payload["community_id"], start, end) is None


def test_exact_threshold_witness_keeps_the_float_branch_difference_in_audit(
    rounded_delivery,
):
    client, db, payload, source = rounded_delivery
    cid = payload["community_id"]
    point = "SYNTHETIC-PRODUCER"
    assert (
        db.upsert_metering_points(
            [
                {
                    "metering_point_id": point,
                    "expected_directions": ["consumption", "production"],
                }
            ]
        )
        == 1
    )
    assert (
        client.post(
            f"/admin/vnb-calculated-values/dietikon/mappings/{point}",
            headers={"X-Admin-Token": "synthetic-admin"},
            json={
                "effective_from": "2026-09-01T00:00:00Z",
                "source_reference": "SYNTHETIC-PROSUMER",
                "confirmed_membership": True,
            },
        ).status_code
        == 201
    )
    root = ET.fromstring(source)
    templates = [
        ET.tostring(block)
        for block in root.findall("MeteringData")
        if block.find("ConsumptionMeteringPoint/VSENationalID") is not None
        and block.find("ConsumptionMeteringPoint/VSENationalID").text
        == "SYNTHETIC-METER-1"
    ]
    for template in templates:
        clone = ET.fromstring(template)
        clone.find("ConsumptionMeteringPoint/VSENationalID").text = point
        root.append(clone)
    values = {
        ("SYNTHETIC-METER-1", "consumption"): ("0.003", "0.003", "0.000"),
        ("SYNTHETIC-CONSUMER-B", "consumption"): ("0.010", "0.004", "0.006"),
        (point, "consumption"): ("0.020", "0.004", "0.016"),
        (point, "production"): ("0.012", "0.011", "0.001"),
    }
    for block in root.findall("MeteringData"):
        identity = block.find("ConsumptionMeteringPoint/VSENationalID")
        direction = "consumption"
        if identity is None:
            identity = block.find("ProductionMeteringPoint/VSENationalID")
            direction = "production"
        code = block.find("Product/ID/VSENationalCode").text
        position = ("8716867000030", "2404050010123", "2404050010124").index(code)
        block.find("Observation/Volume").text = values[(identity.text, direction)][
            position
        ]
    start, end = _window(payload)
    policy = db.get_billing_policy(cid, start, end)
    db.save_billing_policy(
        cid, {**policy, "effective_from": "2026-09-15", "distribution_model": "einfach"}
    )
    assert e66.post_e66(client, ET.tostring(root)).status_code == 201
    result = billing_runner.run_billing_period(cid, start, end, prepared_by="member")
    period = db.get_billing_period(result["period_id"], cid)
    certificate = period["reconciliation"]["precision_policy"]
    assert certificate["intervals"][0]["redistribution_applied"] is False
    assert certificate["intervals"][0]["allocated_kwh"] == "11/1000"
    assert period["reconciliation"]["vnb_allocated_kwh"] == 0.011
    assert period["reconciliation"]["engine_allocated_kwh"] == 0.012
    assert period["reconciliation"]["difference_kwh"] == 0.001
    invoices = db.approve_billing_period(
        result["period_id"], cid, approver_id="producer"
    )
    quantities = {
        (invoice["participant_id"], item["item_type"]): Decimal(
            str(item["quantity_kwh"])
        )
        for invoice in invoices
        for item in invoice["line_items_snapshot"]
        if item["item_type"] in {"consumer_charge", "producer_credit"}
    }
    assert quantities[("member", "consumer_charge")] == Decimal("0.003")
    assert quantities[("consumer-b", "consumer_charge")] == Decimal("0.004")
    assert quantities[("producer", "consumer_charge")] == Decimal("0.004")
    assert quantities[("producer", "producer_credit")] == Decimal("0.011")
