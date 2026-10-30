# SPDX-License-Identifier: AGPL-3.0-or-later
"""Refuse interval allocation errors even when their monthly totals cancel."""

import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import billing_engine
import billing_readings
import billing_runner
from tests import test_billing_precision_postgres as precision
from tests import test_e66_evidence_postgres as e66

interest_database = precision.interest_database
calculated_client = precision.calculated_client
assignment_request = precision.assignment_request
configured_e66 = precision.configured_e66
rounded_delivery = precision.rounded_delivery
pytestmark = pytest.mark.integration


def test_cancelling_interval_allocation_errors_with_equal_period_totals_are_refused(
    rounded_delivery,
):
    client, db, payload, source = rounded_delivery
    root = ET.fromstring(source)
    root.find(".//ReportPeriod/EndDateTime").text = "2026-09-30T22:45:00Z"
    for block in root.findall("MeteringData"):
        block.find("Interval/EndDateTime").text = "2026-09-30T22:45:00Z"
        code = block.find("Product/ID/VSENationalCode").text
        point = block.find("ConsumptionMeteringPoint/VSENationalID")
        values = None
        if point is not None and code != "8716867000030":
            if point.text == "SYNTHETIC-METER-1":
                values = (
                    ["0.300", "0.366", "0.334"]
                    if code == "2404050010123"
                    else ["0.700", "0.634", "0.666"]
                )
            else:
                values = (
                    ["0.700", "0.634", "0.666"]
                    if code == "2404050010123"
                    else ["1.300", "1.366", "1.334"]
                )
        first = block.find("Observation")
        if values:
            first.find("Volume").text = values[0]
        for index in (1, 2):
            observation = ET.fromstring(ET.tostring(first))
            observation.find("Position/Sequence").text = str(index + 1)
            if values:
                observation.find("Volume").text = values[index]
            block.append(observation)
    assert e66.post_e66(client, ET.tostring(root)).status_code == 201
    start = datetime.fromisoformat(payload["period_start"]).astimezone(
        ZoneInfo("Europe/Zurich")
    )
    end = datetime.fromisoformat("2026-10-01T00:45:00+02:00").astimezone(
        ZoneInfo("Europe/Zurich")
    )
    source = db.get_validated_calculated_values(payload["community_id"], start, end)
    frames = billing_readings.frames_from_calculated_source(source)
    independent = billing_engine.generate_billing_summary(
        frames.production, frames.consumption, 0.08, 0.12, "same"
    )
    comparison = billing_readings.reconcile_with_vnb(frames, independent)
    assert comparison["difference_kwh"] == comparison["production_difference_kwh"] == 0
    assert all(
        item["difference_kwh"] == 0 for item in comparison["per_participant"].values()
    )
    assert all(
        item["difference_kwh"] == 0
        for item in comparison["production_per_participant"].values()
    )
    with pytest.raises(billing_runner.BillingRunError) as error:
        billing_runner.run_billing_period(
            payload["community_id"], start, end, prepared_by="member"
        )
    assert error.value.code == "vnb_allocation_mismatch"
    assert db.get_billing_period_for_window(payload["community_id"], start, end) is None
