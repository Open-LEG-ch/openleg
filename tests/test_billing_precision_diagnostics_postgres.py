# SPDX-License-Identifier: AGPL-3.0-or-later
"""The billing cron must separate accounting refusals from material mismatches."""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import billing_reconciliation
import billing_runner
from tests import test_billing_precision_postgres as precision
from tests import test_e66_evidence_postgres as e66

interest_database = e66.interest_database
calculated_client = e66.calculated_client
assignment_request = e66.assignment_request
configured_e66 = e66.configured_e66
rounded_delivery = precision.rounded_delivery
pytestmark = pytest.mark.integration


def _altered_source(source, quantities):
    root = ET.fromstring(source)
    for block in root.findall("MeteringData"):
        identity = block.find("ConsumptionMeteringPoint/VSENationalID")
        if identity is None:
            continue
        volumes = quantities.get(identity.text)
        if volumes is None:
            continue
        code = block.find("Product/ID/VSENationalCode").text
        if code != "8716867000030":
            block.find("Observation/Volume").text = volumes[code]
    return ET.tostring(root)


@pytest.mark.parametrize(
    "problem,quantities",
    [
        (
            "unbalanced",
            {
                "SYNTHETIC-METER-1": {
                    "2404050010123": "0.333",
                    "2404050010124": "0.667",
                },
                "SYNTHETIC-CONSUMER-B": {
                    "2404050010123": "0.666",
                    "2404050010124": "1.334",
                },
            },
        ),
        (
            "model_disagreement",
            {
                "SYNTHETIC-METER-1": {
                    "2404050010123": "0.300",
                    "2404050010124": "0.700",
                },
                "SYNTHETIC-CONSUMER-B": {
                    "2404050010123": "0.700",
                    "2404050010124": "1.300",
                },
            },
        ),
    ],
)
def test_cron_reports_rounded_source_refusals_without_leaking_evidence(
    rounded_delivery, monkeypatch, caplog, problem, quantities
):
    client, db, payload, source = rounded_delivery
    assert e66.post_e66(client, _altered_source(source, quantities)).status_code == 201
    start, end = (
        datetime.fromisoformat(payload[key]).astimezone(ZoneInfo("Europe/Zurich"))
        for key in ("period_start", "period_end")
    )
    if problem == "unbalanced":
        delivery = db.get_validated_calculated_values(
            payload["community_id"], start, end
        )
        assert (
            billing_reconciliation.audit_e66(delivery, model="proportional")["outcome"]
            == "rounding_compatible"
        )
    monkeypatch.setattr(
        billing_runner,
        "previous_complete_month",
        lambda: (start, end),
    )
    client.application.config["CRON_SECRET"] = "synthetic-cron"

    with caplog.at_level(logging.ERROR):
        response = client.post(
            "/api/cron/process-billing",
            headers={"X-Cron-Secret": "synthetic-cron"},
        )

    assert response.status_code == 200
    body = response.get_json()
    assert body["processed"] == 0
    assert body["failed"] == 1
    failure = body["failures"][0]
    assert failure["community_id"] == payload["community_id"]
    assert failure["error"] == "vnb_allocation_mismatch"
    if problem == "unbalanced":
        assert failure["reason"] == "vnb_balancing_policy_required"
    else:
        assert "reason" not in failure
    for leak in (
        "SYNTHETIC-METER-1",
        "SYNTHETIC-CONSUMER-B",
        "SYNTHETIC-PRODUCER",
        "VNB quantities require a reviewed balancing policy",
    ):
        assert leak not in response.get_data(as_text=True)
        assert leak not in caplog.text
    assert "vnb_balancing_policy_required" not in caplog.text
    assert "Traceback" not in caplog.text
    assert "reconciliation" not in caplog.text
    assert db.get_billing_period_for_window(payload["community_id"], start, end) is None
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS count FROM invoices")
        assert cur.fetchone()["count"] == 0
