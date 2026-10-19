# SPDX-License-Identifier: AGPL-3.0-or-later
"""An editable reconciliation marker cannot remove E66 source checks."""

import json
import xml.etree.ElementTree as ET
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import billing_approval
import billing_runner
from tests import test_billing_precision_postgres as precision
from tests import test_e66_evidence_postgres as e66

interest_database = precision.interest_database
calculated_client = precision.calculated_client
assignment_request = precision.assignment_request
configured_e66 = precision.configured_e66
rounded_delivery = precision.rounded_delivery
pytestmark = pytest.mark.integration


def test_removing_precision_marker_cannot_downgrade_source_quantity_checks(
    rounded_delivery,
):
    _, db, payload, _ = rounded_delivery
    period_id, period = precision.prepare_rounded(rounded_delivery)
    reconciliation = deepcopy(period["reconciliation"])
    reconciliation.pop("precision_policy")
    for participant, quantity in (("member", 0.334), ("consumer-b", 0.666)):
        reconciliation["per_participant"][participant] = {
            "engine_kwh": quantity,
            "vnb_kwh": quantity,
            "difference_kwh": 0,
        }
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE billing_line_items SET quantity_kwh=%s, amount_chf=%s * unit_price_chf_per_kwh WHERE billing_period_id=%s AND participant_id=%s AND item_type='consumer_charge'",
                (quantity, quantity, period_id, participant),
            )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE billing_periods SET reconciliation=%s WHERE id=%s",
            (json.dumps(reconciliation), period_id),
        )
    with pytest.raises(billing_approval.BillingApprovalError):
        db.approve_billing_period(
            period_id, payload["community_id"], approver_id="producer"
        )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS count FROM invoices")
        assert cur.fetchone()["count"] == 0


def test_legacy_exact_e66_draft_without_certificate_can_still_be_approved(
    rounded_delivery,
):
    client, db, payload, source = rounded_delivery
    root = ET.fromstring(source)
    for block in root.findall("MeteringData"):
        if block.find("ConsumptionMeteringPoint") is not None:
            block.find("Observation/Volume").text = {
                "8716867000030": "1.500",
                "2404050010123": "0.500",
                "2404050010124": "1.000",
            }[block.find("Product/ID/VSENationalCode").text]
    assert e66.post_e66(client, ET.tostring(root)).status_code == 201
    start, end = (
        datetime.fromisoformat(payload[key]).astimezone(ZoneInfo("Europe/Zurich"))
        for key in ("period_start", "period_end")
    )
    result = billing_runner.run_billing_period(
        payload["community_id"], start, end, prepared_by="member"
    )
    period = db.get_billing_period(result["period_id"], payload["community_id"])
    reconciliation = period["reconciliation"]
    assert all(
        item["difference_kwh"] == 0
        for item in reconciliation["per_participant"].values()
    )
    reconciliation.pop("precision_policy")
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE billing_periods SET reconciliation=%s WHERE id=%s",
            (json.dumps(reconciliation), result["period_id"]),
        )

    invoices = db.approve_billing_period(
        result["period_id"], payload["community_id"], approver_id="producer"
    )

    assert len(invoices) == 3
    assert all(
        "precision_policy" not in invoice["provenance_snapshot"]["reconciliation"]
        for invoice in invoices
    )
