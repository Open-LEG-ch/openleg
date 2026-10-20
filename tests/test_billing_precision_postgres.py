# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bill accepted rounded VNB allocations through the real E66 evidence path."""

import json
import xml.etree.ElementTree as ET
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import billing_approval
import billing_reconciliation
import billing_runner
from tests import test_e66_evidence_postgres as e66

interest_database = e66.interest_database
calculated_client = e66.calculated_client
assignment_request = e66.assignment_request
configured_e66 = e66.configured_e66
pytestmark = pytest.mark.integration


@pytest.fixture
def rounded_delivery(configured_e66, monkeypatch):
    client, db, payload, _ = configured_e66
    monkeypatch.setattr(db, "_db_initialized", True)
    cid = payload["community_id"]
    root = ET.fromstring(e66.synthetic_e66(local="0.333", residual="0.667"))
    template_blocks = [ET.tostring(block) for block in root.findall("MeteringData")]
    for participant, point, direction, quantities in (
        (
            "consumer-b",
            "SYNTHETIC-CONSUMER-B",
            "consumption",
            {
                "8716867000030": "2.000",
                "2404050010123": "0.667",
                "2404050010124": "1.333",
            },
        ),
        (
            "producer",
            "SYNTHETIC-PRODUCER",
            "production",
            {
                "8716867000030": "1.000",
                "2404050010123": "1.000",
                "2404050010124": "0.000",
            },
        ),
    ):
        assert db.save_building(
            participant,
            participant + "@example.invalid",
            {"address": "Synthetic " + participant, "lat": 47.2, "lon": 8.2},
            {},
            verified=True,
            city_id="dietikon",
        )
        assert db.insert_invited_member(cid, participant, "member")
        assert db.confirm_invited_member(cid, participant)
        assert (
            db.upsert_metering_points(
                [
                    {
                        "metering_point_id": point,
                        "community_id": cid,
                        "building_id": participant,
                        "vnb_community_id": "SYNTHETIC-EXTERNAL-LEG",
                        "expected_directions": [direction],
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
                    "source_reference": "SYNTHETIC-MEMBERSHIP",
                    "confirmed_membership": True,
                },
            ).status_code
            == 201
        )
        for source_block in template_blocks:
            block = ET.fromstring(source_block)
            identity = block.find("ConsumptionMeteringPoint")
            if direction == "production":
                identity.tag = "ProductionMeteringPoint"
            identity.find("VSENationalID").text = point
            block.find("Observation/Volume").text = quantities[
                block.find("Product/ID/VSENationalCode").text
            ]
            root.append(block)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE communities SET status = 'active' WHERE community_id = %s", (cid,)
        )
    db.save_billing_policy(
        cid,
        {
            "effective_from": "2026-09-01",
            "internal_price_chf_per_kwh": "0.12",
            "grid_fee_chf_per_kwh": "0.08",
            "settlement_fee_chf_per_kwh": "0",
            "network_level": "same",
            "distribution_model": "proportional",
            "vat_mode": "none",
            "vat_rate_pct": "0",
            "payment_days": 30,
            "invoice_prefix": "SYNTH",
            "delivery_method": "download",
        },
    )
    return client, db, payload, ET.tostring(root)


def test_rounded_e66_quantities_reach_approved_invoice_without_a_second_document(
    rounded_delivery,
):
    client, db, payload, source = rounded_delivery
    response = e66.post_e66(client, source)
    assert response.status_code == 201
    accepted = response.get_json()
    start, end = (
        datetime.fromisoformat(payload[key]).astimezone(ZoneInfo("Europe/Zurich"))
        for key in ("period_start", "period_end")
    )

    result = billing_runner.run_billing_period(
        payload["community_id"], start, end, prepared_by="member"
    )

    assert result["status"] == "created"
    period = db.get_billing_period(result["period_id"], payload["community_id"])
    assert period["calculated_values_fingerprint"] == accepted["fingerprint"]
    invoices = db.approve_billing_period(
        result["period_id"], payload["community_id"], approver_id="producer"
    )
    quantities = {
        (item["participant_id"], item["item_type"]): float(item["quantity_kwh"])
        for invoice in invoices
        for item in invoice["line_items_snapshot"]
        if item["item_type"] in {"consumer_charge", "producer_credit"}
    }
    assert quantities[("member", "consumer_charge")] == 0.333
    assert quantities[("consumer-b", "consumer_charge")] == 0.667
    assert quantities[("producer", "producer_credit")] == 1
    assert period["reconciliation"]["per_participant"]["member"]["engine_kwh"] != 0.333
    assert period["reconciliation"]["per_participant"]["member"]["vnb_kwh"] == 0.333
    assert (
        period["reconciliation"]["precision_policy"]["outcome"] == "rounding_compatible"
    )
    for invoice in invoices:
        own = invoice["participant_id"]
        provenance = invoice["provenance_snapshot"]["reconciliation"]
        assert set(provenance["per_participant"]) == {own}
        assert set(provenance["production_per_participant"]) == {own}
        assert "intervals" not in provenance["precision_policy"]
        assert {
            item["building_id"]
            for item in provenance["source_evidence"]["metadata"]["mappings"]
        } == {own}
    assert (
        client.post(
            f"/admin/vnb-calculated-values/dietikon/{accepted['id']}/withdrawals",
            headers={
                "X-Admin-Token": "synthetic-admin",
                "Idempotency-Key": "issued-retry",
            },
            json={"reason": "Synthetic withdrawal after issue"},
        ).status_code
        == 201
    )
    assert (
        db.approve_billing_period(
            result["period_id"], payload["community_id"], approver_id="producer"
        )
        == invoices
    )


def prepare_rounded(fixture):
    client, db, payload, source = fixture
    assert e66.post_e66(client, source).status_code == 201
    start, end = (
        datetime.fromisoformat(payload[key]).astimezone(ZoneInfo("Europe/Zurich"))
        for key in ("period_start", "period_end")
    )
    result = billing_runner.run_billing_period(
        payload["community_id"], start, end, prepared_by="member"
    )
    return result["period_id"], db.get_billing_period(
        result["period_id"], payload["community_id"]
    )


@pytest.mark.parametrize("forgery", ["certificate", "independent", "source_quantities"])
def test_forged_precision_drafts_do_not_create_invoices(rounded_delivery, forgery):
    _, db, payload, _ = rounded_delivery
    period_id, period = prepare_rounded(rounded_delivery)
    reconciliation = deepcopy(period["reconciliation"])
    if forgery == "certificate":
        reconciliation["precision_policy"]["certificate_sha256"] = "0" * 64
    elif forgery == "independent":
        for entry in reconciliation["per_participant"].values():
            entry["engine_kwh"] = entry["vnb_kwh"]
            entry["difference_kwh"] = 0
    else:
        for participant, quantity in (("member", 0.334), ("consumer-b", 0.666)):
            entry = reconciliation["per_participant"][participant]
            entry["vnb_kwh"] = quantity
            entry["difference_kwh"] = round(entry["engine_kwh"] - quantity, 6)
            with db.get_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE billing_line_items SET quantity_kwh = %s, amount_chf = %s * unit_price_chf_per_kwh WHERE billing_period_id = %s AND participant_id = %s AND item_type = 'consumer_charge'",
                    (quantity, quantity, period_id, participant),
                )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE billing_periods SET reconciliation = %s WHERE id = %s",
            (json.dumps(reconciliation), period_id),
        )

    with pytest.raises(billing_approval.BillingApprovalError):
        db.approve_billing_period(
            period_id, payload["community_id"], approver_id="producer"
        )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS count FROM invoices")
        assert cur.fetchone()["count"] == 0
        cur.execute("SELECT status FROM billing_periods WHERE id = %s", (period_id,))
        assert cur.fetchone()["status"] == "draft"


def test_rounding_policy_keeps_withdrawal_and_dual_control_gates(rounded_delivery):
    client, db, payload, _ = rounded_delivery
    period_id, _ = prepare_rounded(rounded_delivery)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE communities SET require_dual_control = TRUE WHERE community_id = %s",
            (payload["community_id"],),
        )
    with pytest.raises(billing_approval.BillingApprovalError, match="Dual-control"):
        db.approve_billing_period(
            period_id, payload["community_id"], approver_id="member"
        )
    source = db.list_calculated_values_deliveries("dietikon")[0]
    assert (
        client.post(
            f"/admin/vnb-calculated-values/dietikon/{source['id']}/withdrawals",
            headers={
                "X-Admin-Token": "synthetic-admin",
                "Idempotency-Key": "draft-withdrawal",
            },
            json={"reason": "Synthetic source withdrawn before approval"},
        ).status_code
        == 201
    )
    with pytest.raises(billing_approval.BillingApprovalError):
        db.approve_billing_period(
            period_id, payload["community_id"], approver_id="producer"
        )
    assert (
        db.get_billing_period(period_id, payload["community_id"])["status"]
        == "invalidated"
    )


@pytest.mark.parametrize(
    "problem", ["model_disagreement", "unbalanced_quantities", "changed_readings"]
)
def test_unsafe_or_unpriced_source_never_creates_a_draft(rounded_delivery, problem):
    client, db, payload, source = rounded_delivery
    root = ET.fromstring(source)
    if problem != "changed_readings":
        for block in root.findall("MeteringData"):
            identity = block.find("ConsumptionMeteringPoint/VSENationalID")
            if identity is None:
                continue
            point = identity.text
            if problem == "unbalanced_quantities" and point != "SYNTHETIC-CONSUMER-B":
                continue
            code = block.find("Product/ID/VSENationalCode").text
            if code != "8716867000030":
                block.find("Observation/Volume").text = (
                    (
                        {"2404050010123": "0.300", "2404050010124": "0.700"}
                        if point == "SYNTHETIC-METER-1"
                        else {"2404050010123": "0.700", "2404050010124": "1.300"}
                    )
                    if problem == "model_disagreement"
                    else {"2404050010123": "0.666", "2404050010124": "1.334"}
                )[code]
    imported = e66.post_e66(client, ET.tostring(root))
    assert imported.status_code == 201
    start, end = (
        datetime.fromisoformat(payload[key]).astimezone(ZoneInfo("Europe/Zurich"))
        for key in ("period_start", "period_end")
    )
    if problem == "unbalanced_quantities":
        delivery = db.get_validated_calculated_values(
            payload["community_id"], start, end
        )
        assert (
            billing_reconciliation.audit_e66(delivery, model="proportional")["outcome"]
            == "rounding_compatible"
        )
    if problem == "changed_readings":
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE metering_point_readings SET total_kwh = total_kwh + 0.0001 WHERE metering_point_id = 'SYNTHETIC-METER-1'"
            )

    with pytest.raises(billing_runner.BillingRunError) as error:
        billing_runner.run_billing_period(
            payload["community_id"], start, end, prepared_by="member"
        )

    assert error.value.code == "vnb_allocation_mismatch"
    assert db.get_billing_period_for_window(payload["community_id"], start, end) is None


def test_consumer_limited_source_uses_rounded_producer_credits(rounded_delivery):
    client, db, payload, source = rounded_delivery
    point = "SYNTHETIC-CONSUMER-B"
    assert (
        db.upsert_metering_points(
            [{"metering_point_id": point, "expected_directions": ["production"]}]
        )
        == 1
    )
    assert (
        client.post(
            f"/admin/vnb-calculated-values/dietikon/mappings/{point}",
            headers={"X-Admin-Token": "synthetic-admin"},
            json={
                "confirmed_membership": True,
                "source_reference": "SYNTHETIC-PRODUCER-STATEMENT",
                "effective_from": "2026-09-01T00:00:00Z",
            },
        ).status_code
        == 201
    )
    root = ET.fromstring(source)
    channels = {
        "SYNTHETIC-METER-1": ("1.000", "1.000", "0.000"),
        point: ("1.000", "0.333", "0.667"),
        "SYNTHETIC-PRODUCER": ("2.000", "0.667", "1.333"),
    }
    for block in root.findall("MeteringData"):
        identity = block.find("ConsumptionMeteringPoint")
        if identity is None:
            identity = block.find("ProductionMeteringPoint")
        identity_point = identity.find("VSENationalID").text
        if identity_point == point:
            identity.tag = "ProductionMeteringPoint"
        code = block.find("Product/ID/VSENationalCode").text
        position = ("8716867000030", "2404050010123", "2404050010124").index(code)
        block.find("Observation/Volume").text = channels[identity_point][position]
    assert e66.post_e66(client, ET.tostring(root)).status_code == 201
    start, end = (
        datetime.fromisoformat(payload[key]).astimezone(ZoneInfo("Europe/Zurich"))
        for key in ("period_start", "period_end")
    )

    result = billing_runner.run_billing_period(
        payload["community_id"], start, end, prepared_by="member"
    )
    invoices = db.approve_billing_period(
        result["period_id"], payload["community_id"], approver_id="producer"
    )

    credits = {
        item["participant_id"]: float(item["quantity_kwh"])
        for invoice in invoices
        for item in invoice["line_items_snapshot"]
        if item["item_type"] == "producer_credit"
    }
    assert credits["consumer-b"] == 0.333
    assert credits["producer"] == 0.667
    period = db.get_billing_period(result["period_id"], payload["community_id"])
    assert (
        period["reconciliation"]["production_per_participant"]["consumer-b"][
            "difference_kwh"
        ]
        != 0
    )
    assert (
        period["reconciliation"]["precision_policy"]["intervals"][0]["limiting_side"]
        == "consumption"
    )
