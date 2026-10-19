# SPDX-License-Identifier: AGPL-3.0-or-later
"""Retire VNB evidence without erasing the original delivery or issuing invoices."""

import os
from datetime import datetime
from unittest.mock import patch

import psycopg2
import pytest

import billing_approval
from tests import test_interest_postgres
from tests.test_billing_approval import _draft

interest_database = test_interest_postgres.interest_database
pytestmark = pytest.mark.integration


@pytest.fixture
def calculated_client(interest_database):
    db = interest_database
    assert db.save_building(
        "member",
        "member@example.invalid",
        {"address": "Synthetic meter 1", "lat": 47.2, "lon": 8.2},
        {},
        verified=True,
        city_id="dietikon",
    )
    cid = db.create_community_record("Synthetic LEG", "member", "simple", "")
    assert (
        db.upsert_metering_points(
            [
                {
                    "metering_point_id": "SYNTHETIC-METER-1",
                    "community_id": cid,
                    "building_id": "member",
                    "expected_directions": ["consumption"],
                }
            ]
        )
        == 1
    )
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": os.environ["DATABASE_URL"],
            "REDIS_URL": "",
            "ADMIN_TOKEN": "synthetic-admin",
        },
        clear=True,
    ):
        from app import create_app

        app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "synthetic-lifecycle",
                "RATELIMIT_STORAGE_URI": "memory://",
            },
            load_environment=False,
            check_database=False,
        )
        yield (
            app.test_client(),
            db,
            {
                "contract_version": "vnb-calculated-values/1",
                "format_version": "json/1",
                "community_id": cid,
                "period_start": "2026-10-01T00:00:00+02:00",
                "period_end": "2026-10-01T00:15:00+02:00",
                "timezone": "Europe/Zurich",
                "source": "Synthetic VNB",
                "vnb_case_id": "SYNTHETIC-ORIGINAL",
                "unit": "kWh",
                "records": [
                    {
                        "measured_at": "2026-10-01T00:00:00+02:00",
                        "participant_id": "member",
                        "direction": "consumption",
                        "value": "0.25",
                    }
                ],
            },
        )


def test_withdrawal_preserves_source_and_allows_a_corrected_delivery(calculated_client):
    client, db, payload = calculated_client
    headers = {"X-Admin-Token": "synthetic-admin"}
    endpoint = "/admin/vnb-calculated-values/dietikon"
    original_response = client.post(
        endpoint, headers=headers, json={"delivery": payload, "transport": "file"}
    )
    assert original_response.status_code == 201
    original = original_response.get_json()
    start, end = (
        datetime.fromisoformat(payload[key]) for key in ("period_start", "period_end")
    )
    assert "evidence_bytes" not in db.get_validated_calculated_values(
        payload["community_id"], start, end
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT evidence_bytes, evidence_sha256, normalized_records FROM vnb_calculated_values_deliveries WHERE id = %s",
            (original["id"],),
        )
        source = dict(cur.fetchone())
    withdrawal = f"{endpoint}/{original['id']}/withdrawals"
    request_headers = {**headers, "Idempotency-Key": "synthetic-withdrawal-1"}

    response = client.post(
        withdrawal,
        headers=request_headers,
        json={"reason": "Synthetic VNB withdrew the original delivery"},
    )

    assert response.status_code == 201
    assert response.get_json()["action"] == "withdrawn"
    assert response.headers["Cache-Control"] == "no-store"
    start, end = (
        datetime.fromisoformat(payload[key]) for key in ("period_start", "period_end")
    )
    assert (
        db.get_validated_calculated_values(payload["community_id"], start, end) is None
    )
    replay = client.post(
        withdrawal,
        headers=request_headers,
        json={"reason": "Synthetic VNB withdrew the original delivery"},
    )
    assert replay.status_code == 200
    assert replay.get_json()["replayed"] is True
    corrected = {
        **payload,
        "vnb_case_id": "SYNTHETIC-CORRECTED",
        "records": [{**payload["records"][0], "value": "0.30"}],
    }
    replacement = client.post(
        endpoint, headers=headers, json={"delivery": corrected, "transport": "file"}
    )
    assert replacement.status_code == 201
    assert (
        db.get_validated_calculated_values(payload["community_id"], start, end)["id"]
        == replacement.get_json()["id"]
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT evidence_bytes, evidence_sha256, normalized_records FROM vnb_calculated_values_deliveries WHERE id = %s",
            (original["id"],),
        )
        retained = dict(cur.fetchone())
    assert bytes(retained["evidence_bytes"]) == bytes(source["evidence_bytes"])
    assert retained["evidence_sha256"] == source["evidence_sha256"]
    assert retained["normalized_records"] == source["normalized_records"]


def test_withdrawn_draft_keeps_its_inputs_and_allows_a_new_version(calculated_client):
    client, db, payload = calculated_client
    headers = {"X-Admin-Token": "synthetic-admin"}
    endpoint = "/admin/vnb-calculated-values/dietikon"
    delivery = client.post(
        endpoint, headers=headers, json={"delivery": payload}
    ).get_json()
    start, end = (
        datetime.fromisoformat(payload[key]) for key in ("period_start", "period_end")
    )
    summary = {
        "total_production_kwh": 0.25,
        "total_allocated_kwh": 0.25,
        "total_network_discount_chf": 0,
        "input_fingerprint": "a" * 64,
        "source_document_ids": ["SYNTHETIC-READINGS"],
        "calculated_values_fingerprint": delivery["fingerprint"],
    }
    old_id = db.save_billing_period(payload["community_id"], start, end, summary)
    response = client.post(
        f"{endpoint}/{delivery['id']}/withdrawals",
        headers={**headers, "Idempotency-Key": "withdraw-draft-1"},
        json={"reason": "Synthetic corrected VNB delivery follows"},
    )
    assert response.status_code == 201
    old = db.get_billing_period(old_id)
    assert old["status"] == "invalidated"
    assert old["input_fingerprint"] == "a" * 64
    assert old["calculated_values_fingerprint"] == delivery["fingerprint"]
    assert db.get_billing_period_for_window(payload["community_id"], start, end) is None

    corrected = {
        **payload,
        "vnb_case_id": "SYNTHETIC-CORRECTED",
        "records": [{**payload["records"][0], "value": "0.30"}],
    }
    replacement = client.post(endpoint, headers=headers, json={"delivery": corrected})
    assert replacement.status_code == 201
    new_id = db.save_billing_period(
        payload["community_id"],
        start,
        end,
        {
            **summary,
            "input_fingerprint": "b" * 64,
            "total_production_kwh": 0.30,
            "total_allocated_kwh": 0.30,
            "calculated_values_fingerprint": replacement.get_json()["fingerprint"],
        },
    )

    assert new_id != old_id
    assert db.get_billing_period(new_id)["status"] == "draft"
    assert db.get_billing_period(old_id)["status"] == "invalidated"
    with pytest.raises(psycopg2.errors.UniqueViolation):
        db.save_billing_period(
            payload["community_id"],
            start,
            end,
            {**summary, "input_fingerprint": "c" * 64},
        )
    db.create_tables()
    assert (
        db.get_billing_period_for_window(payload["community_id"], start, end)["id"]
        == new_id
    )


def _invoice_evidence(calculated_client):
    client, db, payload = calculated_client
    cid = payload["community_id"]
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE communities SET status = 'active' WHERE community_id = %s", (cid,)
        )
        cur.execute(
            "UPDATE metering_points SET active = FALSE WHERE community_id = %s", (cid,)
        )
    records = []
    for bid, direction, value in (
        ("building-a", "consumption", "13.366667"),
        ("building-b", "production", "13.366670"),
        ("building-c", "consumption", "0.000003"),
    ):
        assert db.save_building(
            bid,
            bid + "@example.invalid",
            {"address": "Synthetic " + bid, "lat": 47.2, "lon": 8.2},
            {},
            verified=True,
            city_id="dietikon",
        )
        assert db.insert_invited_member(cid, bid, "member")
        assert db.confirm_invited_member(cid, bid)
        assert (
            db.upsert_metering_points(
                [
                    {
                        "metering_point_id": "SYNTHETIC-" + bid,
                        "community_id": cid,
                        "building_id": bid,
                        "expected_directions": [direction],
                    }
                ]
            )
            == 1
        )
        records.append(
            {
                "measured_at": payload["period_start"],
                "participant_id": bid,
                "direction": direction,
                "value": value,
            }
        )
    payload = {**payload, "records": records}
    headers = {"X-Admin-Token": "synthetic-admin"}
    endpoint = "/admin/vnb-calculated-values/dietikon"
    response = client.post(endpoint, headers=headers, json={"delivery": payload})
    assert response.status_code == 201
    delivery = response.get_json()
    summary = _draft(community_id=cid)
    summary["billing_policy_snapshot"]["community_id"] = cid
    summary.update(
        total_production_kwh=13.366670,
        total_allocated_kwh=13.366670,
        total_network_discount_chf=0,
        calculated_values_fingerprint=delivery["fingerprint"],
    )
    return payload, delivery, summary


def test_a_late_draft_cannot_approve_with_withdrawn_evidence(calculated_client):
    client, db, _ = calculated_client
    payload, delivery, summary = _invoice_evidence(calculated_client)
    response = client.post(
        f"/admin/vnb-calculated-values/dietikon/{delivery['id']}/withdrawals",
        headers={
            "X-Admin-Token": "synthetic-admin",
            "Idempotency-Key": "withdraw-before-late-save",
        },
        json={"reason": "Synthetic upstream withdrawal"},
    )
    assert response.status_code == 201
    start, end = (
        datetime.fromisoformat(payload[key]) for key in ("period_start", "period_end")
    )
    period_id = db.save_billing_period(payload["community_id"], start, end, summary)

    with pytest.raises(billing_approval.BillingApprovalError, match="VNB"):
        db.approve_billing_period(
            period_id, payload["community_id"], approver_id="member"
        )

    assert db.list_community_invoices(payload["community_id"]) == []


@pytest.mark.parametrize(
    "admin,territory,expected",
    [
        (None, "dietikon", 403),
        ("wrong", "dietikon", 403),
        ("synthetic-admin", "baden", 404),
    ],
)
def test_withdrawal_is_private_and_scoped_by_its_authenticated_path(
    calculated_client, admin, territory, expected
):
    client, db, payload = calculated_client
    delivery = client.post(
        "/admin/vnb-calculated-values/dietikon",
        headers={"X-Admin-Token": "synthetic-admin"},
        json={"delivery": payload},
    ).get_json()
    headers = {"Idempotency-Key": "scope-test"}
    if admin:
        headers["X-Admin-Token"] = admin
    response = client.post(
        f"/admin/vnb-calculated-values/{territory}/{delivery['id']}/withdrawals",
        headers=headers,
        json={"reason": "Synthetic scope test", "territory": "dietikon"},
    )
    assert response.status_code == expected
    assert response.headers.get("Cache-Control") == "no-store"
    assert response.headers.get("Referrer-Policy") == "no-referrer"
    start, end = (
        datetime.fromisoformat(payload[key]) for key in ("period_start", "period_end")
    )
    assert (
        db.get_validated_calculated_values(payload["community_id"], start, end)["id"]
        == delivery["id"]
    )


@pytest.mark.parametrize(
    "key,reason",
    [
        (None, "Synthetic reason"),
        ("bad key", "Synthetic reason"),
        ("good", ""),
        ("good", None),
        ("good", "x" * 1001),
    ],
)
def test_invalid_withdrawal_keeps_current_evidence(calculated_client, key, reason):
    client, db, payload = calculated_client
    headers = {"X-Admin-Token": "synthetic-admin"}
    endpoint = "/admin/vnb-calculated-values/dietikon"
    delivery = client.post(
        endpoint, headers=headers, json={"delivery": payload}
    ).get_json()
    if key is not None:
        headers["Idempotency-Key"] = key
    response = client.post(
        f"{endpoint}/{delivery['id']}/withdrawals",
        headers=headers,
        json={"reason": reason},
    )
    assert response.status_code == 400
    start, end = (
        datetime.fromisoformat(payload[field])
        for field in ("period_start", "period_end")
    )
    assert (
        db.get_validated_calculated_values(payload["community_id"], start, end)["id"]
        == delivery["id"]
    )


def test_withdrawal_replay_conflicts_and_original_replay_cannot_reactivate(
    calculated_client,
):
    client, db, payload = calculated_client
    headers = {"X-Admin-Token": "synthetic-admin"}
    endpoint = "/admin/vnb-calculated-values/dietikon"
    delivery = client.post(
        endpoint, headers=headers, json={"delivery": payload}
    ).get_json()
    url = f"{endpoint}/{delivery['id']}/withdrawals"
    assert (
        client.post(
            url,
            headers={**headers, "Idempotency-Key": "once"},
            json={"reason": "Synthetic original reason"},
        ).status_code
        == 201
    )
    assert (
        client.post(
            url,
            headers={**headers, "Idempotency-Key": "once"},
            json={"reason": "Changed reason"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            url,
            headers={**headers, "Idempotency-Key": "different"},
            json={"reason": "Synthetic original reason"},
        ).status_code
        == 409
    )
    replay = client.post(endpoint, headers=headers, json={"delivery": payload})
    assert replay.status_code == 201
    assert replay.get_json()["replayed"] is True
    assert replay.get_json()["lifecycle_state"] == "withdrawn"
    listing = client.get(endpoint, headers=headers).get_json()["deliveries"]
    assert listing[0]["lifecycle_state"] == "withdrawn"
    assert "evidence_bytes" not in listing[0]
    assert "normalized_records" not in listing[0]
    start, end = (
        datetime.fromisoformat(payload[field])
        for field in ("period_start", "period_end")
    )
    assert (
        db.get_validated_calculated_values(payload["community_id"], start, end) is None
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS count FROM vnb_calculated_values_events")
        assert cur.fetchone()["count"] == 1


def test_late_storage_failure_rolls_back_withdrawal_and_draft_invalidation(
    calculated_client, caplog
):
    client, db, payload = calculated_client
    headers = {"X-Admin-Token": "synthetic-admin"}
    endpoint = "/admin/vnb-calculated-values/dietikon"
    delivery = client.post(
        endpoint, headers=headers, json={"delivery": payload}
    ).get_json()
    start, end = (
        datetime.fromisoformat(payload[field])
        for field in ("period_start", "period_end")
    )
    period_id = db.save_billing_period(
        payload["community_id"],
        start,
        end,
        {
            "total_production_kwh": 0.25,
            "total_allocated_kwh": 0.25,
            "total_network_discount_chf": 0,
            "calculated_values_fingerprint": delivery["fingerprint"],
        },
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("""CREATE FUNCTION reject_synthetic_withdrawal() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.event_type = 'metering.calculated-delivery.withdrawn' THEN
                RAISE EXCEPTION 'private-synthetic-database-material';
            END IF; RETURN NEW; END $$""")
        cur.execute(
            "CREATE TRIGGER synthetic_event_failure BEFORE INSERT ON operator_events FOR EACH ROW EXECUTE FUNCTION reject_synthetic_withdrawal()"
        )
    response = client.post(
        f"{endpoint}/{delivery['id']}/withdrawals",
        headers={**headers, "Idempotency-Key": "rollback"},
        json={"reason": "Synthetic withdrawal"},
    )
    assert response.status_code == 503
    assert response.get_json() == {"error": "calculated_values_unavailable"}
    assert "private-synthetic-database-material" not in response.text + caplog.text
    assert db.get_billing_period(period_id)["status"] == "draft"
    assert (
        db.get_validated_calculated_values(payload["community_id"], start, end)["id"]
        == delivery["id"]
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS count FROM vnb_calculated_values_events")
        assert cur.fetchone()["count"] == 0


@pytest.mark.parametrize("first", ["withdraw", "approve"])
def test_withdrawal_and_approval_serialize_on_the_community(
    calculated_client, monkeypatch, first
):
    _, db, _ = calculated_client
    payload, delivery, summary = _invoice_evidence(calculated_client)
    cid = payload["community_id"]
    start, end = (
        datetime.fromisoformat(payload[field])
        for field in ("period_start", "period_end")
    )
    period_id = db.save_billing_period(cid, start, end, summary)

    def withdraw():
        return db.withdraw_calculated_values_delivery(
            "dietikon",
            delivery["id"],
            "concurrent-withdrawal",
            "Synthetic VNB withdrawal",
        )

    def approve():
        return db.approve_billing_period(period_id, cid, approver_id="member")

    with test_interest_postgres.transaction_race(monkeypatch) as race:
        staged = race.start(
            withdraw if first == "withdraw" else approve, pause_before_commit=True
        )
        race.wait_until_staged(staged)
        waiting = race.start(approve if first == "withdraw" else withdraw)
        try:
            race.wait_until_blocked_by(waiting, staged)
        finally:
            staged.release.set()
        result = race.finish(staged)
        if first == "withdraw":
            with pytest.raises(billing_approval.BillingApprovalError):
                race.finish(waiting)
            assert db.list_community_invoices(cid) == []
            assert db.get_billing_period(period_id)["status"] == "invalidated"
        else:
            assert len(result) == 3
            assert race.finish(waiting)["action"] == "withdrawn"
            assert db.get_billing_period(period_id)["status"] == "issued"
            assert (
                db.approve_billing_period(period_id, cid, approver_id="member")
                == result
            )


@pytest.mark.parametrize("territory", ["dietikon", "baden"])
def test_withdrawal_rejects_historical_tenant_after_owner_moves(
    calculated_client, territory
):
    client, db, payload = calculated_client
    delivery = client.post(
        "/admin/vnb-calculated-values/dietikon",
        headers={"X-Admin-Token": "synthetic-admin"},
        json={"delivery": payload},
    ).get_json()
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE buildings SET city_id = 'baden' WHERE building_id = 'member'"
        )
    response = client.post(
        f"/admin/vnb-calculated-values/{territory}/{delivery['id']}/withdrawals",
        headers={"X-Admin-Token": "synthetic-admin", "Idempotency-Key": "moved-tenant"},
        json={"reason": "Synthetic withdrawal"},
    )
    assert response.status_code == 404
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS count FROM vnb_calculated_values_events")
        assert cur.fetchone()["count"] == 0


def test_a_draft_saved_during_withdrawal_cannot_approve_before_it_commits(
    calculated_client, monkeypatch
):
    _, db, _ = calculated_client
    payload, delivery, summary = _invoice_evidence(calculated_client)
    cid = payload["community_id"]
    start, end = (
        datetime.fromisoformat(payload[field])
        for field in ("period_start", "period_end")
    )
    with test_interest_postgres.transaction_race(monkeypatch) as race:
        withdrawal = race.start(
            lambda: db.withdraw_calculated_values_delivery(
                "dietikon",
                delivery["id"],
                "withdraw-before-save-commit",
                "Synthetic concurrent withdrawal",
            ),
            pause_before_commit=True,
        )
        race.wait_until_staged(withdrawal)
        period_id = db.save_billing_period(cid, start, end, summary)
        approval = race.start(
            lambda: db.approve_billing_period(period_id, cid, approver_id="member")
        )
        try:
            race.wait_until_blocked_by(approval, withdrawal)
        finally:
            withdrawal.release.set()
        assert race.finish(withdrawal)["action"] == "withdrawn"
        with pytest.raises(billing_approval.BillingApprovalError, match="VNB"):
            race.finish(approval)
    assert db.list_community_invoices(cid) == []


def test_withdrawal_preserves_issued_invoice_snapshots(calculated_client):
    client, db, _ = calculated_client
    payload, delivery, summary = _invoice_evidence(calculated_client)
    start, end = (
        datetime.fromisoformat(payload[key]) for key in ("period_start", "period_end")
    )
    period_id = db.save_billing_period(payload["community_id"], start, end, summary)
    issued = db.approve_billing_period(
        period_id, payload["community_id"], approver_id="member"
    )
    assert len(issued) == 3
    response = client.post(
        f"/admin/vnb-calculated-values/dietikon/{delivery['id']}/withdrawals",
        headers={
            "X-Admin-Token": "synthetic-admin",
            "Idempotency-Key": "withdraw-after-issue",
        },
        json={"reason": "Synthetic upstream correction after issue"},
    )
    assert response.status_code == 201
    assert db.get_billing_period(period_id)["status"] == "issued"
    with pytest.raises(psycopg2.errors.UniqueViolation):
        db.save_billing_period(payload["community_id"], start, end, summary)
    assert (
        db.approve_billing_period(
            period_id, payload["community_id"], approver_id="member"
        )
        == issued
    )
    assert (
        db.get_validated_calculated_values(payload["community_id"], start, end) is None
    )
