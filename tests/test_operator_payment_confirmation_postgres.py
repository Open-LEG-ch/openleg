# SPDX-License-Identifier: AGPL-3.0-or-later
"""Operator payment confirmation through HTTP and public PostgreSQL reads."""

import hashlib
from datetime import date
from unittest.mock import patch

import pytest
from flask import Flask

import database as db
import operator_api
from tests import test_interest_postgres
from tests.test_payment_acceptance_postgres import (
    COMMUNITY,
    _confirm,
    _import,
    _invoice,
)

interest_database = test_interest_postgres.interest_database
pytestmark = pytest.mark.integration


@pytest.fixture
def payment_client(interest_database):
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES (%s,'Fictional LEG')",
            (COMMUNITY,),
        )
    db.create_operator_api_client(
        COMMUNITY,
        "operator-a",
        "Test operator",
        ["payments.mutate"],
        hashlib.sha256(b"fictional-token").hexdigest(),
        None,
    )
    for capability in ("billing.read", "payments.read"):
        db.create_operator_api_client(
            COMMUNITY,
            "operator-a",
            capability,
            [capability],
            hashlib.sha256(capability.encode()).hexdigest(),
            "https://receiver.example.org/events",
        )
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(operator_api.operator_api_bp)
    with patch(
        "operator_api.db.operator_creator_is_confirmed_admin", return_value=True
    ):
        yield app


def test_dashboard_replay_recognizes_operator_payment(payment_client):
    invoice_id = _invoice()
    entry_id, _ = _import()
    assert _confirm(payment_client, entry_id, invoice_id).status_code == 200
    assert db.record_invoice_payment(
        invoice_id, COMMUNITY, "operator-a", date(2026, 11, 4), "unknown reference"
    ) == {"lifecycle_state": "paid", "already_recorded": True}
    assert (
        len(
            [
                event
                for event in db.list_invoice_events(invoice_id, COMMUNITY)
                if event["event_type"] == "paid"
            ]
        )
        == 1
    )


def test_concurrent_entries_pay_delivered_invoice_only_once(
    payment_client, monkeypatch
):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from store import billing

    invoice_id = _invoice()
    first_id, _ = _import()
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO bank_statement_entries(
                statement_import_id,community_id,entry_reference,booking_date,
                amount,currency,payment_reference,is_reversal,
                credit_debit_indicator,match_decision,amount_basis,booking_status)
               SELECT statement_import_id,community_id,'second-entry',booking_date+10,
                amount,currency,'separate reference',is_reversal,
                credit_debit_indicator,match_decision,amount_basis,booking_status
               FROM bank_statement_entries WHERE id=%s RETURNING id""",
            (first_id,),
        )
        second_id = cur.fetchone()["id"]
    barrier = Barrier(3)
    # Exercise the invoice lock independently of LEG booking serialisation.
    monkeypatch.setattr(billing, "_lock_payment_booking", lambda cur, community: None)

    def confirm(entry_id):
        barrier.wait(timeout=10)
        return _confirm(payment_client, entry_id, invoice_id, f"entry-{entry_id}")

    # Hold the invoice until BOTH HTTP transactions have begun their locking
    # statements. This forces the stale-subquery race without scheduling luck.
    with ThreadPoolExecutor(max_workers=2) as executor:
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM invoices WHERE id=%s FOR UPDATE", (invoice_id,))
            futures = [
                executor.submit(confirm, entry) for entry in (first_id, second_id)
            ]
            try:
                barrier.wait(timeout=10)
                deadline = time.monotonic() + 10
                while True:
                    cur.execute("SELECT pg_stat_clear_snapshot()")
                    cur.execute(
                        """SELECT count(*) AS n FROM pg_stat_activity
                           WHERE datname=current_database() AND pid<>pg_backend_pid()
                           AND wait_event_type='Lock'
                           AND wait_event IN ('transactionid', 'tuple')
                           AND query LIKE '%FOR UPDATE OF i%'"""
                    )
                    if cur.fetchone()["n"] == 2:
                        break
                    assert time.monotonic() < deadline, (
                        "both requests must wait on the invoice row lock"
                    )
                    time.sleep(0.01)
            finally:
                conn.commit()
        responses = [future.result(timeout=10) for future in futures]
    assert sorted(r.status_code for r in responses) == [200, 409]
    loser = next(r for r in responses if r.status_code == 409)
    assert loser.get_json() == {
        "code": "invalid_payment",
        "error": "Invalid payment confirmation",
        "retryable": False,
        "schema_version": "operator-api/1",
    }
    from store.operator_operations import list_payments

    entries, _ = list_payments(COMMUNITY)
    assert sorted(e["match_decision"] for e in entries) == ["matched", "unmatched"]
    assert (
        next(e for e in entries if e["match_decision"] == "unmatched")["invoice_id"]
        is None
    )
    assert (
        len(
            [
                e
                for e in db.list_invoice_events(invoice_id, COMMUNITY)
                if e["event_type"] == "paid"
            ]
        )
        == 1
    )


def test_payment_preserves_invoice_and_records_actual_previous_state(payment_client):
    from tests.test_store_billing_postgres import MEMBER

    invoice_id = _invoice()
    before = db.get_invoice_for_participant(invoice_id, MEMBER)
    entry_id, _ = _import()
    assert _confirm(payment_client, entry_id, invoice_id).status_code == 200
    after = db.get_invoice_for_participant(invoice_id, MEMBER)
    assert after.pop("lifecycle_state") == "paid"
    before.pop("lifecycle_state")
    assert after == before
    paid = [
        e
        for e in db.list_invoice_events(invoice_id, COMMUNITY)
        if e["event_type"] == "paid"
    ]
    assert len(paid) == 1
    assert paid[0]["previous_state"] == "delivered"
    assert paid[0]["new_state"] == "paid"


def test_payment_notifies_billing_and_payment_subscribers_once(payment_client):
    invoice_id = _invoice()
    entry_id, _ = _import()
    assert _confirm(payment_client, entry_id, invoice_id).status_code == 200
    deliveries = db.list_operator_webhook_deliveries(COMMUNITY)
    assert sorted(d["event_type"] for d in deliveries) == [
        "invoice.paid",
        "payment.match.confirmed",
    ]
    assert next(d for d in deliveries if d["event_type"] == "invoice.paid")[
        "aggregate_id"
    ] == str(invoice_id)


def test_request_replay_is_stable_and_payload_reuse_conflicts(payment_client):
    invoice_id = _invoice()
    entry_id, _ = _import()
    first = _confirm(payment_client, entry_id, invoice_id)
    replay = _confirm(payment_client, entry_id, invoice_id)
    assert first.status_code == replay.status_code == 200
    assert replay.get_json() == {**first.get_json(), "replayed": True}
    conflict = _confirm(payment_client, entry_id, invoice_id + 1)
    assert conflict.status_code == 409
    assert conflict.get_json() == {
        "code": "idempotency_conflict",
        "error": "Invalid payment confirmation",
        "retryable": False,
        "schema_version": "operator-api/1",
    }
    assert len(db.list_operator_webhook_deliveries(COMMUNITY)) == 2
    assert (
        len(
            [
                e
                for e in db.list_invoice_events(invoice_id, COMMUNITY)
                if e["event_type"] == "paid"
            ]
        )
        == 1
    )


@pytest.mark.parametrize(
    "guard",
    [
        "underpayment",
        "overpayment",
        "currency",
        "reversal",
        "debit",
        "issued",
        "cancelled",
        "foreign_invoice",
        "foreign_entry",
    ],
)
def test_ineligible_payment_leaves_entry_and_invoice_untouched(payment_client, guard):
    from store.operator_operations import list_payments
    from tests.test_store_billing_postgres import MEMBER, _insert_issued_invoice

    invoice_id = (
        _insert_issued_invoice("ISSUED-ONLY") if guard == "issued" else _invoice()
    )
    if guard == "cancelled":
        db.cancel_invoice(invoice_id, COMMUNITY, "operator-a", "Incorrect invoice")
    overrides = {
        "underpayment": {"amount": "9.66"},
        "overpayment": {"amount": "9.68"},
        "currency": {"currency": "EUR"},
        "reversal": {"is_reversal": True},
        "debit": {"direction": "DBIT"},
    }.get(guard, {})
    entry_id, _ = _import(**overrides)
    if guard == "reversal":
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE bank_statement_entries SET match_decision='unmatched' WHERE id=%s",
                (entry_id,),
            )
    if guard in {"foreign_invoice", "foreign_entry"}:
        with db.get_connection() as conn, conn.cursor() as cur:
            if guard == "foreign_invoice":
                cur.execute(
                    """INSERT INTO invoices(community_id,participant_id,invoice_number,status,
                        policy_snapshot,provenance_snapshot,line_items_snapshot,
                        net_chf,vat_rate_pct,vat_chf,gross_chf,issue_date,due_date)
                       SELECT 'foreign',participant_id,'FOREIGN',status,
                        policy_snapshot,provenance_snapshot,line_items_snapshot,
                        net_chf,vat_rate_pct,vat_chf,gross_chf,issue_date,due_date
                       FROM invoices WHERE id=%s RETURNING id""",
                    (invoice_id,),
                )
                invoice_id = cur.fetchone()["id"]
                cur.execute(
                    """INSERT INTO invoice_lifecycle_events(
                       invoice_id,community_id,actor_id,event_type,previous_state,
                       new_state,idempotency_key)
                       VALUES (%s,'foreign','operator-a','delivered','issued','delivered','delivered')""",
                    (invoice_id,),
                )
            else:
                cur.execute(
                    "UPDATE bank_statement_entries SET community_id='foreign' WHERE id=%s",
                    (entry_id,),
                )
    community = "foreign" if guard == "foreign_entry" else COMMUNITY
    before_deliveries = db.list_operator_webhook_deliveries(COMMUNITY)
    before_entries = list_payments(community)
    before_invoice = db.get_invoice_for_participant(invoice_id, MEMBER)
    response = _confirm(payment_client, entry_id, invoice_id)
    assert response.status_code == (404 if guard == "foreign_entry" else 409)
    assert response.get_json() == {
        "code": "not_found" if guard == "foreign_entry" else "invalid_payment",
        "error": "Resource not found or not eligible"
        if guard == "foreign_entry"
        else "Invalid payment confirmation",
        "retryable": False,
        "schema_version": "operator-api/1",
    }
    assert list_payments(community) == before_entries
    assert db.get_invoice_for_participant(invoice_id, MEMBER) == before_invoice
    assert db.list_operator_webhook_deliveries(COMMUNITY) == before_deliveries
