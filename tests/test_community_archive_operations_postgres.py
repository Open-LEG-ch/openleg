# SPDX-License-Identifier: AGPL-3.0-or-later
"""Move financial operations and replay history with their owning LEG."""

import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg2.extras
import pytest

import billing_runner
import community_archive
import payment_reconciliation
from tests import test_community_archive_evidence_postgres as evidence
from tests.test_community_archive import MemoryArchiveStore
from tests.test_dashboard_access_routes import _set_session
from tests.test_payment_reconciliation import CAMT_053

interest_database = evidence.interest_database
calculated_client = evidence.calculated_client
assignment_request = evidence.assignment_request
configured_e66 = evidence.configured_e66
rounded_delivery = evidence.rounded_delivery
pytestmark = pytest.mark.integration

TABLES = (
    "bank_statement_imports",
    "bank_statement_entries",
    "invoice_queries",
    "invoice_query_messages",
    "invoice_query_events",
    "community_role_events",
    "community_batteries",
    "community_battery_shares",
    "operator_events",
    "operator_action_idempotency",
)


@pytest.fixture
def operating_archive(rounded_delivery):
    client, db, payload, source = rounded_delivery
    assert evidence.e66.post_e66(client, source).status_code == 201
    cid = payload["community_id"]
    start, end = (
        datetime.fromisoformat(payload[key]).astimezone(ZoneInfo("Europe/Zurich"))
        for key in ("period_start", "period_end")
    )
    period = billing_runner.run_billing_period(cid, start, end, prepared_by="member")
    invoices = db.approve_billing_period(
        period["period_id"], cid, approver_id="producer"
    )
    own = next(invoice for invoice in invoices if invoice["participant_id"] == "member")
    query_id = db.open_invoice_query(
        own["id"],
        "member",
        "amount",
        "Synthetic invoice question",
        "evidence.pdf",
        b"%PDF-synthetic-archive-evidence",
    )
    assert query_id
    source_client = db.create_operator_api_client(
        cid,
        "member",
        "PRIVATE-SOURCE-CLIENT",
        ["cases.read"],
        hashlib.sha256(b"olk_synthetic-source-secret").hexdigest(),
        "https://source-only.invalid/hooks",
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO operator_api_usage(client_id,endpoint) VALUES (%s,'PRIVATE-SOURCE-CLIENT')",
            (source_client["id"],),
        )
    answer = db.respond_operator_invoice_case(
        query_id,
        cid,
        "member",
        "Synthetic operator answer",
        "acknowledged",
        "archive-answer",
    )
    assert answer["status"] == "acknowledged"
    bank = payment_reconciliation.import_statement(
        cid, "member", "synthetic.xml", CAMT_053, db
    )
    assert bank["duplicate"] is False
    bank["public_entries"] = db.list_bank_statement_entries(cid)
    assert db.set_member_access_roles(cid, "consumer-b", ["auditor"], "member")
    db.save_battery(
        cid,
        {
            "participant_id": "battery-meter",
            "capacity_kwh": "45",
            "annual_cost_chf": "120",
            "shares": {"member": "60", "consumer-b": "40"},
        },
    )
    foreign = db.create_community_record("PRIVATE-FOREIGN-LEG", "member", "simple", "")
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE community_members SET status='left' WHERE community_id=%s AND building_id='consumer-b'",
            (cid,),
        )
        cur.execute(
            "INSERT INTO operator_events(event_id,event_type,schema_version,aggregate_id,community_id,payload) VALUES ('foreign-event','invoice.case.updated','operator-event/1','foreign-case',%s,%s)",
            (foreign, psycopg2.extras.Json({"private": "PRIVATE-FOREIGN-PAYLOAD"})),
        )
        cur.execute(
            "INSERT INTO operator_action_idempotency(community_id,action,idempotency_key,request_hash,response) VALUES (%s,'case.respond:foreign','foreign-key',%s,%s)",
            (
                foreign,
                "f" * 64,
                psycopg2.extras.Json({"private": "PRIVATE-FOREIGN-PAYLOAD"}),
            ),
        )
    expected = {}
    with db.get_connection() as conn, conn.cursor() as cur:
        for table in TABLES:
            if table in {"invoice_query_messages", "invoice_query_events"}:
                cur.execute(
                    f"SELECT * FROM {table} WHERE query_id=%s ORDER BY 1", (query_id,)
                )
            else:
                cur.execute(
                    f"SELECT * FROM {table} WHERE community_id=%s ORDER BY 1", (cid,)
                )
            expected[table] = [dict(row) for row in cur.fetchall()]
            assert expected[table], table
    archive = community_archive.export_community_archive(cid)
    assert b"PRIVATE-FOREIGN" not in archive
    assert b"PRIVATE-SOURCE-CLIENT" not in archive
    assert b"source-only.invalid" not in archive
    assert (
        hashlib.sha256(b"olk_synthetic-source-secret").hexdigest().encode()
        not in archive
    )
    return client, db, cid, own, query_id, bank, answer, expected, archive


def test_operating_records_and_api_replay_survive_clean_restore(
    operating_archive, monkeypatch
):
    client, db, cid, own, query_id, bank, answer, expected, archive = operating_archive
    with evidence.clean_destination(db, monkeypatch):
        result = community_archive.restore_community_archive(archive)
        assert result["valid"], result
        assert community_archive.restore_community_archive(archive)["restored"] is True
        with db.get_connection() as conn, conn.cursor() as cur:
            for table, rows in expected.items():
                cur.execute(f"SELECT * FROM {table} ORDER BY 1")
                assert [dict(row) for row in cur.fetchall()] == rows
            for table in (
                "operator_api_clients",
                "operator_webhook_deliveries",
                "operator_api_usage",
            ):
                cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
                assert cur.fetchone()["n"] == 0
        assert (
            db.list_invoice_queries(own["id"], participant_id="member")[0]["status"]
            == "acknowledged"
        )
        assert db.get_battery(cid)["capacity_kwh"] == 45
        assert db.list_bank_statement_entries(cid) == bank["public_entries"]
        repeated_bank = payment_reconciliation.import_statement(
            cid, "member", "synthetic.xml", CAMT_053, db
        )
        assert repeated_bank["duplicate"] is True
        assert repeated_bank["entries"] == bank["entries"]
        _set_session(client, "member", csrf_token="archive-csrf")
        enrollment = client.post(
            f"/leg/community/{cid}/operator-api/credentials",
            headers={"X-CSRF-Token": "archive-csrf"},
            json={
                "name": "New destination client",
                "capabilities": ["cases.read", "cases.mutate"],
            },
        )
        assert enrollment.status_code == 201, enrollment.get_json()
        token = enrollment.get_json()["token"]
        replay = client.post(
            f"/api/operator/v1/communities/{cid}/billing/cases/{query_id}/responses",
            headers={
                "Authorization": "Bearer " + token,
                "Idempotency-Key": "archive-answer",
            },
            json={"message": "Synthetic operator answer", "status": "acknowledged"},
        )
        assert replay.status_code == 200, replay.get_json()
        assert replay.get_json() == {
            "schema_version": "operator-api/1",
            **answer,
            "replayed": True,
        }
        assert (
            len(
                db.list_invoice_queries(own["id"], participant_id="member")[0][
                    "messages"
                ]
            )
            == 2
        )


@pytest.mark.parametrize(
    "table,column,value",
    [
        ("bank_statement_entries", "statement_import_id", 999999),
        ("invoice_queries", "invoice_id", 999999),
        ("invoice_query_messages", "query_id", 999999),
        ("community_role_events", "actor_building_id", "outsider"),
        ("community_battery_shares", "building_id", "outsider"),
    ],
)
def test_foreign_operating_parent_references_are_rejected(
    operating_archive, monkeypatch, table, column, value
):
    _, db, _, _, _, _, _, _, original = operating_archive
    archive = json.loads(original)
    archive["datasets"][table][0][column] = value
    evidence._rehash(archive, table)
    recording_store = MemoryArchiveStore()
    rejected = community_archive.restore_community_archive(
        json.dumps(archive), store=recording_store
    )
    assert rejected["valid"] is False
    assert recording_store.restore_calls == 0
    with evidence.clean_destination(db, monkeypatch):
        result = community_archive.restore_community_archive(json.dumps(archive))
        assert result["valid"] is False
        assert result["errors"]
        assert db.fetch_user_communities("member") == []
