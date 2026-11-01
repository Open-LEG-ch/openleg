# SPDX-License-Identifier: AGPL-3.0-or-later
"""Strict refusal through authenticated E66 ingestion and native storage."""

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

import billing_reconciliation
import billing_runner
from tests import test_billing_einfach_precision_postgres as einfach
from tests import test_billing_precision_postgres as precision
from tests import test_e66_evidence_postgres as e66

interest_database = e66.interest_database
calculated_client = e66.calculated_client
assignment_request = e66.assignment_request
configured_e66 = e66.configured_e66
rounded_delivery = precision.rounded_delivery
einfach_delivery = einfach.einfach_delivery
pytestmark = pytest.mark.integration


@pytest.mark.parametrize("model", ["proportional", "einfach"])
@pytest.mark.parametrize(
    "produced,local",
    [("4.000", "1.333"), ("5.000", "1.667")],
    ids=["production-more", "consumption-more"],
)
def test_certified_unequal_e66_persists_neither_draft_nor_invoices(
    einfach_delivery, model, produced, local
):
    client, db, payload, source = einfach_delivery
    cid = payload["community_id"]
    root = ET.fromstring(source)
    for block in root.findall("MeteringData"):
        production = block.find("ProductionMeteringPoint") is not None
        total, community = (produced, produced) if production else ("4.000", local)
        channels = {
            "8716867000030": total,
            "2404050010123": community,
            "2404050010124": str(Decimal(total) - Decimal(community)),
        }
        code = block.find("Product/ID/VSENationalCode").text
        block.find("Observation/Volume").text = channels[code]
    assert e66.post_e66(client, ET.tostring(root)).status_code == 201
    start, end = (
        datetime.fromisoformat(payload[key]) for key in ("period_start", "period_end")
    )
    policy = db.get_billing_policy(cid, start, end)
    db.save_billing_policy(
        cid,
        {
            **policy,
            "distribution_model": model,
            "effective_from": (start - timedelta(days=1)).date().isoformat(),
        },
    )
    accepted = db.get_validated_calculated_values(cid, start, end)
    assert (
        billing_reconciliation.audit_e66(accepted, model=model)["outcome"]
        == "rounding_compatible"
    )
    totals = {
        direction: sum(
            Decimal(row["source_quantities"]["community_kwh"])
            for row in accepted["normalized_records"]
            if row["direction"] == direction
        )
        for direction in ("consumption", "production")
    }
    assert abs(totals["production"] - totals["consumption"]) == Decimal("0.001")
    assert abs(totals["production"] - totals["consumption"]) / totals[
        "consumption"
    ] < Decimal("0.001")

    with pytest.raises(billing_runner.BillingRunError) as refused:
        billing_runner.run_billing_period(cid, start, end)

    assert refused.value.code == "vnb_allocation_mismatch"
    assert refused.value.reason == "vnb_balancing_policy_required"
    assert db.get_billing_period_for_window(cid, start, end) is None
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS count FROM invoices WHERE community_id = %s", (cid,)
        )
        assert cur.fetchone()["count"] == 0
