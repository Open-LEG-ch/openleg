# SPDX-License-Identifier: AGPL-3.0-or-later
"""An editable reconciliation marker cannot remove E66 source checks."""

import json
from copy import deepcopy

import pytest

import billing_approval
from tests import test_billing_precision_postgres as precision

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
