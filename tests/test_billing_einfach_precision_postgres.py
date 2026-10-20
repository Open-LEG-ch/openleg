# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bill accepted rounded VNB allocations under the einfach model through real E66 evidence."""

import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

import billing_runner
from tests import test_billing_precision_postgres as precision
from tests import test_e66_evidence_postgres as e66

interest_database = e66.interest_database
calculated_client = e66.calculated_client
assignment_request = e66.assignment_request
configured_e66 = e66.configured_e66
rounded_delivery = precision.rounded_delivery
pytestmark = pytest.mark.integration


@pytest.fixture
def einfach_delivery(rounded_delivery):
    client, db, payload, source = rounded_delivery
    cid = payload["community_id"]
    assert db.save_building(
        "consumer-c",
        "consumer-c@example.invalid",
        {"address": "Synthetic consumer-c", "lat": 47.2, "lon": 8.2},
        {},
        verified=True,
        city_id="dietikon",
    )
    assert db.insert_invited_member(cid, "consumer-c", "member")
    assert db.confirm_invited_member(cid, "consumer-c")
    assert (
        db.upsert_metering_points(
            [
                {
                    "metering_point_id": "SYNTHETIC-CONSUMER-C",
                    "community_id": cid,
                    "building_id": "consumer-c",
                    "vnb_community_id": "SYNTHETIC-EXTERNAL-LEG",
                    "expected_directions": ["consumption"],
                }
            ]
        )
        == 1
    )
    assert (
        client.post(
            "/admin/vnb-calculated-values/dietikon/mappings/SYNTHETIC-CONSUMER-C",
            headers={"X-Admin-Token": "synthetic-admin"},
            json={
                "effective_from": "2026-09-01T00:00:00Z",
                "source_reference": "SYNTHETIC-MEMBERSHIP",
                "confirmed_membership": True,
            },
        ).status_code
        == 201
    )
    root = ET.fromstring(source)
    for template in [
        block
        for block in root.findall("MeteringData")
        if block.find("ConsumptionMeteringPoint") is not None
        and block.find("ConsumptionMeteringPoint/VSENationalID").text
        == "SYNTHETIC-METER-1"
    ]:
        clone = ET.fromstring(ET.tostring(template))
        clone.find(
            "ConsumptionMeteringPoint/VSENationalID"
        ).text = "SYNTHETIC-CONSUMER-C"
        root.append(clone)
    quantities = {
        "SYNTHETIC-METER-1": {
            "8716867000030": "0.200",
            "2404050010123": "0.200",
            "2404050010124": "0.000",
        },
        "SYNTHETIC-CONSUMER-B": {
            "8716867000030": "1.000",
            "2404050010123": "0.314",
            "2404050010124": "0.686",
        },
        "SYNTHETIC-CONSUMER-C": {
            "8716867000030": "3.000",
            "2404050010123": "0.486",
            "2404050010124": "2.514",
        },
    }
    for block in root.findall("MeteringData"):
        identity = block.find("ConsumptionMeteringPoint")
        if identity is None:
            continue
        point = identity.find("VSENationalID").text
        if point not in quantities:
            continue
        code = block.find("Product/ID/VSENationalCode").text
        block.find("Observation/Volume").text = quantities[point][code]
    db.save_billing_policy(
        cid,
        {
            "effective_from": "2026-09-15",
            "internal_price_chf_per_kwh": "0.12",
            "grid_fee_chf_per_kwh": "0.08",
            "settlement_fee_chf_per_kwh": "0",
            "network_level": "same",
            "distribution_model": "einfach",
            "vat_mode": "none",
            "vat_rate_pct": "0",
            "payment_days": 30,
            "invoice_prefix": "SYNTH",
            "delivery_method": "download",
        },
    )
    return client, db, payload, ET.tostring(root)


def test_einfach_precision_reaches_approved_invoice_from_reported_total_witness(
    einfach_delivery,
):
    client, db, payload, source = einfach_delivery
    response = e66.post_e66(client, source)
    assert response.status_code == 201
    accepted = response.get_json()
    start, end = (
        datetime.fromisoformat(payload[key]).astimezone(ZoneInfo("Europe/Zurich"))
        for key in ("period_start", "period_end")
    )

    policy = db.get_billing_policy(payload["community_id"], start, end)
    assert policy is not None and policy["distribution_model"] == "einfach"

    result = billing_runner.run_billing_period(
        payload["community_id"], start, end, prepared_by="member"
    )

    assert result["status"] == "created"
    period = db.get_billing_period(result["period_id"], payload["community_id"])
    assert period["distribution_model"] == policy["distribution_model"]
    assert period["calculated_values_fingerprint"] == accepted["fingerprint"]
    reconciliation = period["reconciliation"]
    certificate = reconciliation["precision_policy"]
    assert certificate["version"] == "vnb-einfach-e66-reported-total-witness/1"
    assert certificate["model"] == "einfach"
    assert certificate["witness_basis"] == "reported_total_channels"
    gaps = reconciliation["per_participant"]
    assert float(gaps["consumer-b"]["engine_kwh"]) == 0.314286
    assert float(gaps["consumer-b"]["vnb_kwh"]) == 0.314
    assert float(gaps["consumer-c"]["engine_kwh"]) == 0.485714
    assert float(gaps["consumer-c"]["vnb_kwh"]) == 0.486

    invoices = db.approve_billing_period(
        result["period_id"], payload["community_id"], approver_id="producer"
    )
    quantities = {
        (invoice["participant_id"], item["item_type"]): Decimal(
            str(item["quantity_kwh"])
        )
        for invoice in invoices
        for item in invoice["line_items_snapshot"]
        if item["item_type"] in {"consumer_charge", "producer_credit"}
    }
    assert quantities[("member", "consumer_charge")] == Decimal("0.200")
    assert quantities[("consumer-b", "consumer_charge")] == Decimal("0.314")
    assert quantities[("consumer-c", "consumer_charge")] == Decimal("0.486")
    assert quantities[("producer", "consumer_charge")] == 0
    assert quantities[("producer", "producer_credit")] == Decimal("1.000")
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
        db.approve_billing_period(
            result["period_id"], payload["community_id"], approver_id="producer"
        )
        == invoices
    )
