# SPDX-License-Identifier: AGPL-3.0-or-later
"""A released imbalance tolerance on the exact E66 source path, end to end.

The synthetic source credits 1.000 kWh against 0.999 kWh charged, about 0.1%
apart and just above the default tolerance. Without a release the run stops
and records the figures; with a release the draft bills the exact source
consumption, the producer carries the residue, and approval re-derives the
same credits from the source and the tolerance frozen in the draft.
"""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

import billing_approval
import billing_runner
from tests import test_billing_precision_diagnostics_postgres as diagnostics
from tests import test_billing_precision_postgres as precision
from tests import test_e66_evidence_postgres as e66

interest_database = e66.interest_database
calculated_client = e66.calculated_client
assignment_request = e66.assignment_request
configured_e66 = e66.configured_e66
rounded_delivery = precision.rounded_delivery
pytestmark = pytest.mark.integration

UNBALANCED = {
    "SYNTHETIC-METER-1": {"2404050010123": "0.333", "2404050010124": "0.667"},
    "SYNTHETIC-CONSUMER-B": {"2404050010123": "0.666", "2404050010124": "1.334"},
}


@pytest.fixture
def unbalanced_period(rounded_delivery):
    client, db, payload, source = rounded_delivery
    altered = diagnostics._altered_source(source, UNBALANCED)
    assert e66.post_e66(client, altered).status_code == 201
    start, end = (
        datetime.fromisoformat(payload[key]).astimezone(ZoneInfo("Europe/Zurich"))
        for key in ("period_start", "period_end")
    )
    return db, payload["community_id"], start, end


def test_without_a_release_the_period_is_blocked_with_its_figures(unbalanced_period):
    db, community_id, start, end = unbalanced_period

    with pytest.raises(billing_runner.BillingRunError) as blocked:
        billing_runner.run_billing_period(community_id, start, end)

    assert blocked.value.code == "vnb_imbalance_exceeds_tolerance"
    assert db.get_billing_period_for_window(community_id, start, end) is None
    (block,) = db.list_billing_imbalance_blocks(community_id)
    assert block["community_consumption_kwh"] == Decimal("0.999000")
    assert block["community_production_kwh"] == Decimal("1.000000")
    assert block["imbalance_kwh"] == Decimal("0.001000")
    assert block["imbalance_ratio"] == Decimal("0.001002")
    assert block["tolerance_ratio"] == Decimal("0.001000")


def test_a_released_tolerance_bills_and_approves_the_exact_source(
    unbalanced_period,
):
    db, community_id, start, end = unbalanced_period
    with pytest.raises(billing_runner.BillingRunError):
        billing_runner.run_billing_period(community_id, start, end)
    # The workspace releases the window as PostgreSQL returns it, in UTC.
    (block,) = db.list_billing_imbalance_blocks(community_id)
    start, end = block["period_start"], block["period_end"]
    db.save_billing_imbalance_release(
        community_id, start, end, Decimal("0.002"), "BKW Ticket 4711", "member"
    )

    prepared = billing_runner.run_billing_period(
        community_id, start, end, prepared_by="member"
    )

    assert prepared["status"] == "created"
    period = db.get_billing_period(prepared["period_id"], community_id)
    assert period["prepared_by"] == "member"
    reconciliation = period["reconciliation"]
    assert reconciliation["vnb_imbalance_tolerance_ratio"] == 0.002
    assert reconciliation["vnb_imbalance_release"]["reason"] == "BKW Ticket 4711"
    assert "precision_policy" in reconciliation
    quantities = {
        item_type: sum(
            (
                Decimal(str(item["quantity_kwh"]))
                for item in period["line_items"]
                if item["item_type"] == item_type
            ),
            Decimal(0),
        )
        for item_type in ("consumer_charge", "producer_credit")
    }
    assert quantities["consumer_charge"] == Decimal("0.999")
    assert quantities["producer_credit"] == Decimal("0.999")
    assert db.list_billing_imbalance_blocks(community_id) == []

    invoices = db.approve_billing_period(
        prepared["period_id"], community_id, approver_id="producer"
    )

    credited = {
        invoice["participant_id"]: Decimal(str(invoice["net_chf"]))
        for invoice in invoices
    }
    assert credited["producer"] < 0
    # Members' invoices keep the release's record and ratio, not who raised it
    # or why.
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT provenance_snapshot FROM invoices WHERE billing_period_id = %s",
            (prepared["period_id"],),
        )
        snapshots = [row["provenance_snapshot"] for row in cur.fetchall()]
    assert snapshots
    for snapshot in snapshots:
        release = snapshot["reconciliation"]["vnb_imbalance_release"]
        assert set(release) == {"id", "max_imbalance_ratio"}


def test_a_release_below_the_measured_imbalance_keeps_the_period_blocked(
    unbalanced_period,
):
    db, community_id, start, end = unbalanced_period
    db.save_billing_imbalance_release(
        community_id, start, end, Decimal("0.001"), "zu knapp", "member"
    )

    with pytest.raises(billing_runner.BillingRunError) as blocked:
        billing_runner.run_billing_period(community_id, start, end)

    assert blocked.value.code == "vnb_imbalance_exceeds_tolerance"
    assert db.get_billing_period_for_window(community_id, start, end) is None


def test_dual_control_refuses_the_person_who_raised_the_tolerance(
    unbalanced_period,
):
    db, community_id, start, end = unbalanced_period
    with pytest.raises(billing_runner.BillingRunError):
        billing_runner.run_billing_period(community_id, start, end)
    db.save_billing_imbalance_release(
        community_id, start, end, Decimal("0.002"), "BKW Ticket 4711", "member"
    )
    prepared = billing_runner.run_billing_period(
        community_id, start, end, prepared_by="member"
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE communities SET require_dual_control = TRUE WHERE community_id = %s",
            (community_id,),
        )
    # Someone else re-prepares the draft; the releaser still may not approve it.
    assert db.record_billing_period_preparer(
        prepared["period_id"], community_id, "producer"
    )

    with pytest.raises(billing_approval.BillingApprovalError, match="Dual-control"):
        db.approve_billing_period(
            prepared["period_id"], community_id, approver_id="member"
        )


@pytest.mark.parametrize(
    "problem",
    [
        "ratio_only",
        "missing",
        "foreign_community",
        "foreign_window",
        "ratio",
        "actor",
        "reason",
        "released_at",
    ],
)
def test_approval_rejects_release_evidence_not_matching_the_persisted_row(
    unbalanced_period, problem
):
    import json
    from datetime import timedelta

    db, community_id, start, end = unbalanced_period
    db.save_billing_imbalance_release(
        community_id, start, end, Decimal("0.002"), "BKW Ticket 4711", "member"
    )
    prepared = billing_runner.run_billing_period(
        community_id, start, end, prepared_by="producer"
    )
    period_id = prepared["period_id"]
    period = db.get_billing_period(period_id, community_id)
    reconciliation = period["reconciliation"]
    release = reconciliation["vnb_imbalance_release"]
    if problem == "ratio_only":
        reconciliation["vnb_imbalance_release"] = {"max_imbalance_ratio": "0.002"}
    elif problem == "missing":
        release["id"] = 2_000_000_000
    elif problem == "foreign_community":
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO communities (community_id, name, status) VALUES (%s, %s, 'active')",
                ("synthetic-foreign-release", "Synthetic foreign LEG"),
            )
        other = db.save_billing_imbalance_release(
            "synthetic-foreign-release",
            start,
            end,
            Decimal("0.002"),
            "BKW Ticket 4711",
            "member",
        )
        release["id"] = other["id"]
    elif problem == "foreign_window":
        other = db.save_billing_imbalance_release(
            community_id,
            start + timedelta(days=1),
            end + timedelta(days=1),
            Decimal("0.002"),
            "BKW Ticket 4711",
            "member",
        )
        release["id"] = other["id"]
    elif problem == "ratio":
        # The draft still carries a self-consistent ratio; its actual row differs.
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE billing_imbalance_releases SET max_imbalance_ratio = 0.003 WHERE id = %s",
                (release["id"],),
            )
    elif problem == "actor":
        release["released_by"] = "forged-releaser"
    elif problem == "reason":
        release["reason"] = "another reason"
    else:
        release["released_at"] = "2026-01-01T00:00:00+00:00"
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE communities SET require_dual_control = TRUE WHERE community_id = %s",
            (community_id,),
        )
        cur.execute(
            "UPDATE billing_periods SET reconciliation = %s::jsonb WHERE id = %s",
            (json.dumps(reconciliation), period_id),
        )

    with pytest.raises(billing_approval.BillingApprovalError):
        db.approve_billing_period(period_id, community_id, approver_id="consumer-b")

    assert db.get_billing_period(period_id, community_id)["status"] == "draft"
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS count FROM invoices WHERE billing_period_id = %s",
            (period_id,),
        )
        assert cur.fetchone()["count"] == 0
