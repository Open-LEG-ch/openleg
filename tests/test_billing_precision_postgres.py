# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bill accepted rounded VNB allocations through the real E66 evidence path."""

import json
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from html import unescape
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

import billing_approval
import billing_reconciliation
import billing_runner
import community_access
from tests import test_e66_evidence_postgres as e66
from tests.test_dashboard_access_routes import _set_session

interest_database = e66.interest_database
calculated_client = e66.calculated_client
assignment_request = e66.assignment_request
configured_e66 = e66.configured_e66
pytestmark = pytest.mark.integration
DUAL_CONTROL_MESSAGE = (
    "Vorbereitung und Freigabe müssen durch verschiedene Personen erfolgen."
)


@pytest.fixture
def rounded_delivery(configured_e66, monkeypatch, request):
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
            participant + "@example.org",
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
            "UPDATE buildings SET email = 'member@example.org' WHERE building_id = 'member'"
        )
        cur.execute(
            "UPDATE communities SET status = 'active' WHERE community_id = %s", (cid,)
        )
    db.save_billing_policy(
        cid,
        {
            "effective_from": "2026-09-01",
            "internal_price_chf_per_kwh": "0.12",
            "grid_fee_chf_per_kwh": "0.08",
            "settlement_fee_chf_per_kwh": getattr(request, "param", "0"),
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


@pytest.mark.parametrize("rounded_delivery", ["0.03"], indirect=True)
def test_nonzero_settlement_fee_reaches_approved_rounded_e66_invoices(rounded_delivery):
    client, _db, payload, source = rounded_delivery
    assert e66.post_e66(client, source).status_code == 201
    start, end = (
        datetime.fromisoformat(payload[key]).astimezone(ZoneInfo("Europe/Zurich"))
        for key in ("period_start", "period_end")
    )

    prepared = billing_runner.run_billing_period(
        payload["community_id"], start, end, prepared_by="member"
    )
    invoices = billing_approval.approve_billing_period(
        prepared["period_id"], payload["community_id"], approver_id="producer"
    )

    fees = {
        invoice["participant_id"]: tuple(
            Decimal(str(item[key]))
            for key in ("quantity_kwh", "unit_price_chf_per_kwh", "amount_chf")
        )
        for invoice in invoices
        for item in invoice["line_items_snapshot"]
        if item["item_type"] == "settlement_fee"
    }
    assert fees == {
        "member": (Decimal("0.333"), Decimal("0.03"), Decimal("0.009990")),
        "consumer-b": (Decimal("0.667"), Decimal("0.03"), Decimal("0.020010")),
        "producer": (Decimal(0), Decimal("0.03"), Decimal(0)),
    }
    consumers = {invoice["participant_id"]: invoice for invoice in invoices}
    for person, total in (("member", "0.05"), ("consumer-b", "0.10")):
        invoice = consumers[person]
        assert Decimal(str(invoice["net_chf"])) == Decimal(total)
        assert Decimal(str(invoice["vat_chf"])) == 0
        assert Decimal(str(invoice["gross_chf"])) == Decimal(total)


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
    invoices = billing_approval.approve_billing_period(
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
    source_evidence = period["reconciliation"]["source_evidence"]
    audit_fields = {
        "document_status",
        "evidence_sha256",
        "sender_id",
        "receiver_id",
        "document_created_at",
        "vnb_community_id",
        "precision",
        "quality_policy",
        "channel_balance_policy",
        "products",
    }
    # Issued invoices retain the accepted document's audit facts while limiting
    # participant mappings to the invoiced member.
    assert audit_fields <= source_evidence["metadata"].keys()
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
        expected_evidence = deepcopy(source_evidence)
        expected_evidence["metadata"] = {
            key: source_evidence["metadata"][key] for key in audit_fields
        }
        expected_evidence["metadata"]["mappings"] = [
            mapping
            for mapping in source_evidence["metadata"]["mappings"]
            if mapping["building_id"] == own
        ]
        assert provenance["source_evidence"] == expected_evidence
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
        billing_approval.approve_billing_period(
            result["period_id"], payload["community_id"], approver_id="producer"
        )
        == invoices
    )


def prepare_rounded(fixture, *, prepared_by="member"):
    client, db, payload, source = fixture
    assert e66.post_e66(client, source).status_code == 201
    start, end = (
        datetime.fromisoformat(payload[key]).astimezone(ZoneInfo("Europe/Zurich"))
        for key in ("period_start", "period_end")
    )
    result = billing_runner.run_billing_period(
        payload["community_id"], start, end, prepared_by=prepared_by
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
        billing_approval.approve_billing_period(
            period_id, payload["community_id"], approver_id="producer"
        )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS count FROM invoices")
        assert cur.fetchone()["count"] == 0
        cur.execute("SELECT status FROM billing_periods WHERE id = %s", (period_id,))
        assert cur.fetchone()["status"] == "draft"


@pytest.mark.parametrize("preparer", ["system", "unknown", "consumer-b", "producer"])
def test_dual_control_rejects_ineligible_preparer(rounded_delivery, preparer):
    client, db, payload, _ = rounded_delivery
    cid = payload["community_id"]
    assert db.set_community_dual_control(cid, True, "member")
    period_id, _ = prepare_rounded(rounded_delivery, prepared_by=None)
    if preparer == "producer":
        # Even a role with preparation rights must belong to a confirmed member.
        assert db.set_member_access_roles(
            cid, "producer", [community_access.BILLING_PREPARER], "member"
        )
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE community_members SET status = 'invited' "
                "WHERE community_id = %s AND building_id = 'producer'",
                (cid,),
            )
    elif preparer == "consumer-b":
        assert db.create_community_record("Other LEG", preparer, "simple", "")
    # Exercise existing rows with an invalid preparer recorded.
    assert db.record_billing_period_preparer(period_id, cid, preparer)

    with pytest.raises(billing_approval.BillingApprovalError, match="Dual-control"):
        billing_approval.approve_billing_period(period_id, cid, approver_id="member")

    _set_session(client, "member")
    workspace = f"/leg/community/{cid}/billing"
    approve = f"{workspace}/period/{period_id}/approve"
    page = client.get(workspace)
    assert page.status_code == 200
    assert approve not in page.text
    assert f"{workspace}/period/{period_id}/prepare" in page.text
    response = client.post(
        approve, data={"csrf_token": "csrf-secret", "confirm_approval": "yes"}
    )
    assert response.status_code == 403
    assert DUAL_CONTROL_MESSAGE in response.text
    assert db.get_billing_period(period_id, cid)["status"] == "draft"
    assert db.list_community_invoices(cid) == []


@pytest.mark.parametrize(
    "preparer_role", [community_access.ADMIN, community_access.BILLING_PREPARER]
)
def test_automated_draft_can_be_submitted_for_separate_approval(
    rounded_delivery, preparer_role
):
    client, db, payload, _ = rounded_delivery
    cid = payload["community_id"]
    assert db.set_community_dual_control(cid, True, "member")
    assert db.set_member_access_roles(cid, "producer", [preparer_role], "member")
    period_id, period = prepare_rounded(rounded_delivery, prepared_by=None)
    assert period["prepared_by"] is None
    if preparer_role == community_access.ADMIN:
        assert db.record_billing_period_preparer(period_id, cid, "system")
    workspace = f"/leg/community/{cid}/billing"
    prepare = f"{workspace}/period/{period_id}/prepare"
    approve = f"{workspace}/period/{period_id}/approve"
    approval_form = {"csrf_token": "csrf-secret", "confirm_approval": "yes"}

    _set_session(client, "producer")
    page = client.get(workspace)
    assert page.status_code == 200
    assert prepare in page.text
    assert approve not in page.text
    assert (
        client.post(
            prepare, data={"csrf_token": "csrf-secret", "confirm_prepare": "yes"}
        ).status_code
        == 302
    )
    assert db.get_billing_period(period_id, cid)["prepared_by"] == "producer"

    with pytest.raises(billing_approval.BillingApprovalError, match="Dual-control"):
        billing_approval.approve_billing_period(period_id, cid, approver_id="producer")
    assert approve not in client.get(workspace).text
    assert client.post(approve, data=approval_form).status_code == 403
    assert db.list_community_invoices(cid) == []

    _set_session(client, "member")
    assert approve in client.get(workspace).text
    assert client.post(approve, data=approval_form).status_code == 302
    assert db.get_billing_period(period_id, cid)["status"] == "issued"
    assert len(db.list_community_invoices(cid)) == 3


def _enable_dual_control_with_second_admin(rounded_delivery):
    _, db, payload, _ = rounded_delivery
    cid = payload["community_id"]
    assert db.set_community_dual_control(cid, True, "member")
    assert db.set_member_access_roles(
        cid, "producer", [community_access.ADMIN], "member"
    )
    return cid


def _give_admins_one_mailbox(db, *, stored_address, login_address):
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "UPDATE buildings SET email = %s WHERE building_id = %s",
            [(stored_address, "producer"), (login_address, "member")],
        )


@pytest.mark.parametrize("interest_database", ["C.utf8"], indirect=True)
@pytest.mark.parametrize(
    ("stored_address", "login_address"),
    [
        ("İ@example.org", "i@example.org"),
        (" MEMBER@Example.ORG ", "member@example.org"),
    ],
)
def test_dual_control_operation_rejects_shared_sign_in_address(
    rounded_delivery, stored_address, login_address
):
    client, db, _, _ = rounded_delivery
    cid = _enable_dual_control_with_second_admin(rounded_delivery)
    period_id, _ = prepare_rounded(rounded_delivery, prepared_by="producer")
    _give_admins_one_mailbox(
        db, stored_address=stored_address, login_address=login_address
    )

    _set_session(client, "member")
    workspace = f"/leg/community/{cid}/billing"
    page = client.get(workspace)
    assert page.status_code == 200

    with pytest.raises(billing_approval.DualControlApprovalError, match="Dual-control"):
        billing_approval.approve_billing_period(period_id, cid, approver_id="member")

    assert f'action="{workspace}/period/{period_id}/approve"' not in page.text
    assert db.get_billing_period(period_id, cid)["status"] == "draft"
    assert db.list_community_invoices(cid) == []


@pytest.mark.parametrize("interest_database", ["C.utf8"], indirect=True)
@pytest.mark.parametrize(
    ("stored_address", "login_address"),
    [("İ@example.org", "i@example.org"), ("MEMBER@Example.ORG", "member@example.org")],
)
def test_dual_control_rejects_sessions_from_one_mailbox(
    rounded_delivery, monkeypatch, stored_address, login_address
):
    import app

    client, db, _, _ = rounded_delivery
    cid = _enable_dual_control_with_second_admin(rounded_delivery)
    period_id, _ = prepare_rounded(rounded_delivery, prepared_by=None)
    _give_admins_one_mailbox(
        db, stored_address=stored_address, login_address=login_address
    )
    send = Mock()
    monkeypatch.setattr(app, "send_email", send)
    assert (
        client.post(
            "/dashboard/access/request", data={"email": f" {login_address.upper()} "}
        ).status_code
        == 200
    )
    assert send.call_count == 2
    sessions = {}
    for call in send.call_args_list:
        recipient, _, body = call.args
        assert recipient == login_address
        link = re.search(r"/dashboard/access/[A-Za-z0-9_-]+", body).group()
        session_client = client.application.test_client()
        assert session_client.get(link).status_code == 302
        with session_client.session_transaction() as state:
            sessions[state["dashboard_building_id"]] = (
                session_client,
                state["dashboard_csrf_token"],
            )
    assert set(sessions) == {"member", "producer"}
    preparer, prepare_csrf = sessions["producer"]
    approver, approve_csrf = sessions["member"]
    workspace = f"/leg/community/{cid}/billing"
    approve = f"{workspace}/period/{period_id}/approve"
    assert (
        preparer.post(
            f"{workspace}/period/{period_id}/prepare",
            data={"csrf_token": prepare_csrf, "confirm_prepare": "yes"},
        ).status_code
        == 302
    )
    assert db.get_billing_period(period_id, cid)["prepared_by"] == "producer"

    response = approver.post(
        approve, data={"csrf_token": approve_csrf, "confirm_approval": "yes"}
    )
    assert response.status_code == 403
    assert DUAL_CONTROL_MESSAGE in response.text
    assert approve not in approver.get(workspace).text
    assert db.list_community_invoices(cid) == []


def test_dual_control_retries_contended_preparer_and_refuses_deleted_preparer(
    rounded_delivery,
):
    _, db, _, _ = rounded_delivery
    cid = _enable_dual_control_with_second_admin(rounded_delivery)
    period_id, _ = prepare_rounded(rounded_delivery, prepared_by="producer")

    with ThreadPoolExecutor(max_workers=1) as executor:
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM community_members "
                "WHERE community_id = %s AND building_id = 'producer'",
                (cid,),
            )
            attempt = executor.submit(
                billing_approval.approve_billing_period,
                period_id,
                cid,
                approver_id="member",
            )
            with pytest.raises(billing_approval.BillingApprovalConflict, match="retry"):
                attempt.result(timeout=5)

    with pytest.raises(billing_approval.DualControlApprovalError, match="Dual-control"):
        billing_approval.approve_billing_period(period_id, cid, approver_id="member")
    assert db.get_billing_period(period_id, cid)["status"] == "draft"
    assert db.list_community_invoices(cid) == []


@pytest.mark.parametrize("dual_control", [True, False])
def test_billing_approval_rechecks_committed_approver_revocation(
    rounded_delivery, monkeypatch, dual_control
):
    client, db, _, _ = rounded_delivery
    cid = _enable_dual_control_with_second_admin(rounded_delivery)
    assert db.set_community_dual_control(cid, dual_control, "member")
    period_id, _ = prepare_rounded(rounded_delivery, prepared_by="producer")
    _set_session(client, "member")
    workspace = f"/leg/community/{cid}/billing"
    approve = f"{workspace}/period/{period_id}/approve"
    assert approve in client.get(workspace).text
    operation = billing_approval.approve_billing_period

    def revoke_after_dashboard_check(*args, **kwargs):
        # Hold a separate connection open across issuance after committing.
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE community_members SET role = 'member', access_roles = '["auditor"]'
                   WHERE community_id = %s AND building_id = 'member'""",
                (cid,),
            )
            conn.commit()
            return operation(*args, **kwargs)

    monkeypatch.setattr(
        billing_approval, "approve_billing_period", revoke_after_dashboard_check
    )
    response = client.post(
        approve, data={"csrf_token": "csrf-secret", "confirm_approval": "yes"}
    )
    assert response.status_code == 403
    assert db.get_billing_period(period_id, cid)["status"] == "draft"
    assert db.list_community_invoices(cid) == []


@pytest.mark.parametrize(
    ("lang", "retry_message"),
    [
        (
            "de-CH",
            "Ein anderer Vorgang ändert gerade die Berechtigungen. Bitte erneut versuchen.",
        ),
        ("en", "Another operation is updating permissions. Please try again."),
        (
            "fr-CH",
            "Une autre opération est en train de modifier les autorisations. Veuillez réessayer.",
        ),
        ("it-CH", "Un'altra operazione sta modificando le autorizzazioni. Riprova."),
    ],
)
@pytest.mark.parametrize("dual_control", [True, False])
def test_billing_approval_retries_contended_approver(
    rounded_delivery, monkeypatch, dual_control, lang, retry_message
):
    client, db, _, _ = rounded_delivery
    monkeypatch.setitem(client.application.config, "SITE_RELEASE_ENABLED", True)
    cid = _enable_dual_control_with_second_admin(rounded_delivery)
    assert db.set_community_dual_control(cid, dual_control, "member")
    period_id, _ = prepare_rounded(rounded_delivery, prepared_by="producer")
    _set_session(client, "member")
    approve = f"/leg/community/{cid}/billing/period/{period_id}/approve?lang={lang}"
    approval = {"csrf_token": "csrf-secret", "confirm_approval": "yes"}

    with ThreadPoolExecutor(max_workers=1) as executor:
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT 1 FROM community_members
                   WHERE community_id = %s AND building_id = 'member' FOR UPDATE""",
                (cid,),
            )
            response = executor.submit(client.post, approve, data=approval).result(
                timeout=5
            )
            assert response.status_code == 409
            assert retry_message in unescape(response.text)
    assert db.get_billing_period(period_id, cid)["status"] == "draft"
    assert db.list_community_invoices(cid) == []
    assert client.post(approve, data=approval).status_code == 302
    assert len(db.list_community_invoices(cid)) == 3


def test_approval_without_dual_control_ignores_nonparticipant_profile_lock(
    rounded_delivery,
):
    client, db, payload, _ = rounded_delivery
    cid = payload["community_id"]
    approver = "billing-admin"
    assert db.save_building(
        approver,
        "billing-admin@example.org",
        {"address": "Synthetic Administrator", "lat": 47.2, "lon": 8.2},
        {},
        verified=True,
        city_id="dietikon",
    )
    assert db.insert_invited_member(cid, approver, "member")
    assert db.confirm_invited_member(cid, approver)
    assert db.set_member_access_roles(cid, approver, [community_access.ADMIN], "member")
    assert db.set_member_access_roles(cid, "member", [], approver)
    period_id, _ = prepare_rounded(rounded_delivery, prepared_by=None)
    _set_session(client, approver)
    approve = f"/leg/community/{cid}/billing/period/{period_id}/approve"

    with ThreadPoolExecutor(max_workers=1) as executor:
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE buildings SET phone = '+41445550123' WHERE building_id = %s",
                (approver,),
            )
            response = executor.submit(
                client.post,
                approve,
                data={"csrf_token": "csrf-secret", "confirm_approval": "yes"},
            ).result(timeout=5)
            assert response.status_code == 302
    invoices = db.list_community_invoices(cid)
    assert {invoice["participant_id"] for invoice in invoices} == {
        "member",
        "consumer-b",
        "producer",
    }


def test_dual_control_explains_membership_change_during_approval(
    rounded_delivery, monkeypatch
):
    client, db, _, _ = rounded_delivery
    cid = _enable_dual_control_with_second_admin(rounded_delivery)
    period_id, _ = prepare_rounded(rounded_delivery, prepared_by="producer")
    _set_session(client, "member")
    workspace = f"/leg/community/{cid}/billing"
    approve = f"{workspace}/period/{period_id}/approve"
    assert approve in client.get(workspace).text
    operation = billing_approval.approve_billing_period

    def revoke_before_issuing(*args, **kwargs):
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM community_members "
                "WHERE community_id = %s AND building_id = 'producer'",
                (cid,),
            )
        return operation(*args, **kwargs)

    monkeypatch.setattr(
        billing_approval, "approve_billing_period", revoke_before_issuing
    )
    response = client.post(
        approve + "?lang=fr-CH",
        data={"csrf_token": "csrf-secret", "confirm_approval": "yes"},
    )
    assert response.status_code == 409
    assert (
        "La préparation et l'approbation doivent être effectuées par différentes personnes."
        in unescape(response.text)
    )
    assert f'action="{approve}"' not in response.text
    assert db.list_community_invoices(cid) == []


def test_repreparing_replaces_the_eligible_preparer_and_refuses_self_approval(
    rounded_delivery,
):
    client, db, _, _ = rounded_delivery
    cid = _enable_dual_control_with_second_admin(rounded_delivery)
    period_id, _ = prepare_rounded(rounded_delivery, prepared_by="producer")
    _set_session(client, "member")
    workspace = f"/leg/community/{cid}/billing"
    approve = f"{workspace}/period/{period_id}/approve"
    assert approve in client.get(workspace).text
    assert (
        client.post(
            f"{workspace}/period/{period_id}/prepare",
            data={"csrf_token": "csrf-secret", "confirm_prepare": "yes"},
        ).status_code
        == 302
    )
    assert db.get_billing_period(period_id, cid)["prepared_by"] == "member"
    with pytest.raises(billing_approval.DualControlApprovalError, match="Dual-control"):
        billing_approval.approve_billing_period(period_id, cid, approver_id="member")
    assert approve not in client.get(workspace).text
    response = client.post(
        approve, data={"csrf_token": "csrf-secret", "confirm_approval": "yes"}
    )
    assert response.status_code == 403
    assert DUAL_CONTROL_MESSAGE in response.text
    assert db.list_community_invoices(cid) == []


def test_automated_draft_can_be_approved_without_dual_control(rounded_delivery):
    client, db, payload, _ = rounded_delivery
    cid = payload["community_id"]
    period_id, period = prepare_rounded(rounded_delivery, prepared_by=None)
    assert period["prepared_by"] is None
    _set_session(client, "member")
    workspace = f"/leg/community/{cid}/billing"
    approve = f"{workspace}/period/{period_id}/approve"
    assert approve in client.get(workspace).text
    assert (
        client.post(
            approve, data={"csrf_token": "csrf-secret", "confirm_approval": "yes"}
        ).status_code
        == 302
    )
    assert len(db.list_community_invoices(cid)) == 3


def test_rounding_policy_keeps_withdrawal_and_dual_control_gates(rounded_delivery):
    client, db, payload, _ = rounded_delivery
    period_id, _ = prepare_rounded(rounded_delivery)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE communities SET require_dual_control = TRUE WHERE community_id = %s",
            (payload["community_id"],),
        )
    with pytest.raises(billing_approval.BillingApprovalError, match="Dual-control"):
        billing_approval.approve_billing_period(
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
        billing_approval.approve_billing_period(
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
    if problem == "unbalanced_quantities":
        assert error.value.reason == "vnb_balancing_policy_required"
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
    invoices = billing_approval.approve_billing_period(
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
