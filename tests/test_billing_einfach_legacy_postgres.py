# SPDX-License-Identifier: AGPL-3.0-or-later
"""An exact pre-certificate einfach draft stays idempotent and approvable on retry."""

import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

import billing_engine
import billing_policy
import billing_readings
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


@pytest.fixture
def exact_legacy_inputs(einfach_delivery):
    client, db, payload, source = einfach_delivery
    root = ET.fromstring(source)
    channels = {
        "consumption": {
            "8716867000030": "1.000",
            "2404050010123": "1.000",
            "2404050010124": "0.000",
        },
        "production": {
            "8716867000030": "3.000",
            "2404050010123": "3.000",
            "2404050010124": "0.000",
        },
    }
    for block in root.findall("MeteringData"):
        direction = (
            "consumption"
            if block.find("ConsumptionMeteringPoint") is not None
            else "production"
        )
        code = block.find("Product/ID/VSENationalCode").text
        block.find("Observation/Volume").text = channels[direction][code]
    response = e66.post_e66(client, ET.tostring(root))
    assert response.status_code == 201
    start, end = (
        datetime.fromisoformat(payload[key]).astimezone(ZoneInfo("Europe/Zurich"))
        for key in ("period_start", "period_end")
    )
    return client, db, payload, start, end


def legacy_summary(db, community_id, start, end):
    """Rebuild the summary the runner saved before the precision certificate."""
    frames = billing_readings.load_period_frames(community_id, start, end)
    calculated = db.get_validated_calculated_values(
        community_id,
        frames.provenance["period_start"],
        frames.provenance["period_end"],
    )
    frames = billing_readings.with_calculated_vnb_evidence(frames, calculated)
    policy = db.get_billing_policy(community_id, start, end)
    assert policy is not None and policy["distribution_model"] == "einfach"
    policy = {**policy, "community_id": community_id}
    summary = billing_engine.generate_billing_summary(
        frames.production,
        frames.consumption,
        grid_fee_per_kwh=policy["grid_fee_chf_per_kwh"],
        internal_price_per_kwh=policy["internal_price_chf_per_kwh"],
        network_level=policy["network_level"],
        distribution_model=policy["distribution_model"],
        settlement_fee_per_kwh=policy["settlement_fee_chf_per_kwh"],
    )
    reconciliation = billing_readings.reconcile_with_vnb(frames, summary)
    summary.update(
        input_fingerprint=billing_runner._fingerprint(
            frames, policy, summary, reconciliation
        ),
        source_document_ids=list(frames.provenance["source_document_ids"]),
        reconciliation=reconciliation,
        timezone=frames.provenance["timezone"],
        calculated_values_fingerprint=frames.provenance.get(
            "calculated_values_fingerprint"
        ),
        billing_policy_snapshot=dict(policy),
        prepared_by="member",
    )
    return summary


@pytest.fixture
def legacy_period(exact_legacy_inputs):
    client, db, payload, start, end = exact_legacy_inputs
    community_id = payload["community_id"]
    period_id = db.save_billing_period(
        community_id, start, end, legacy_summary(db, community_id, start, end)
    )
    stored = db.get_billing_period(period_id, community_id)
    assert stored["status"] == "draft"
    return client, db, payload, period_id, stored


def test_exact_pre_certificate_einfach_draft_retry_stays_idempotent(legacy_period):
    _client, db, payload, period_id, stored = legacy_period
    community_id = payload["community_id"]
    start, end = (
        datetime.fromisoformat(payload[key]).astimezone(ZoneInfo("Europe/Zurich"))
        for key in ("period_start", "period_end")
    )

    result = billing_runner.run_billing_period(
        community_id, start, end, prepared_by="member"
    )

    assert result == {"status": "already_processed", "period_id": period_id}
    assert db.get_billing_period(period_id, community_id) == stored
    assert "precision_policy" not in stored["reconciliation"]

    original_policy = db.get_billing_policy(community_id, start, end)
    superseding = {
        field: original_policy[field] for field in billing_policy.EDITABLE_POLICY_FIELDS
    }
    superseding["effective_from"] = "2026-09-20"
    superseding["internal_price_chf_per_kwh"] = "0.13"
    db.save_billing_policy(community_id, superseding)

    with pytest.raises(billing_runner.BillingRunError) as error:
        billing_runner.run_billing_period(
            community_id, start, end, prepared_by="member"
        )
    assert error.value.code == "billing_inputs_changed"
    assert db.get_billing_period(period_id, community_id) == stored

    invoices = db.approve_billing_period(
        period_id, community_id, approver_id="producer"
    )
    assert len(invoices) == 4
    quantities = {
        (invoice["participant_id"], item["item_type"]): Decimal(
            str(item["quantity_kwh"])
        )
        for invoice in invoices
        for item in invoice["line_items_snapshot"]
        if item["item_type"] in {"consumer_charge", "producer_credit"}
    }
    assert quantities[("member", "consumer_charge")] == Decimal("1.000")
    assert quantities[("consumer-b", "consumer_charge")] == Decimal("1.000")
    assert quantities[("consumer-c", "consumer_charge")] == Decimal("1.000")
    assert quantities[("producer", "consumer_charge")] == 0
    assert quantities[("producer", "producer_credit")] == Decimal("3.000")
    for invoice in invoices:
        assert Decimal(
            str(invoice["policy_snapshot"]["internal_price_chf_per_kwh"])
        ) == Decimal("0.12")
        provenance = invoice["provenance_snapshot"]["reconciliation"]
        assert "precision_policy" not in provenance
