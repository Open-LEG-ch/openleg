# SPDX-License-Identifier: AGPL-3.0-or-later
"""Store-level tenant scoping, retry eligibility and idempotency for #611."""

import json
import uuid
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import pytest

import billing_lifecycle
import database
from store import operator_operations as store
from store.operator_api import event_id_for
from tests.test_billing_lifecycle import (
    _connection,
    _issued_invoice,
    _LifecycleCursor,
)

COMMUNITY = "community-a"


class Cursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.executed = []
        self.rowcount = 1

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def connection(cursor):
    class Connection:
        def cursor(self):
            return cursor

    @contextmanager
    def factory():
        yield Connection()

    return factory


def test_job_listing_scopes_every_row_to_the_community_tenant(monkeypatch):
    cursor = Cursor([{"id": 7, "status": "failure"}])
    monkeypatch.setattr(database, "get_connection", connection(cursor))

    rows, next_cursor = store.list_metering_jobs(COMMUNITY, status="failure", limit=1)

    assert rows == [{"id": 7, "status": "failure"}]
    assert next_cursor is None
    query, params = cursor.executed[0]
    assert "JOIN communities c ON c.community_id=%s" in query
    assert "r.territory=b.city_id" in query
    assert "owner.city_id=r.territory" in query
    assert params == (COMMUNITY, "failure", "failure", 0, 2)


def test_invoice_case_and_payment_lists_carry_the_community_guard(monkeypatch):
    invoices = Cursor(
        [
            {"id": 3, "invoice_number": "LEG-3"},
            {"id": 4, "invoice_number": "LEG-4"},
        ]
    )
    monkeypatch.setattr(database, "get_connection", connection(invoices))

    rows, next_cursor = store.list_invoices(COMMUNITY, limit=1)

    assert rows == [{"id": 3, "invoice_number": "LEG-3"}]
    assert next_cursor == "3"
    query, params = invoices.executed[0]
    assert "FROM invoices i WHERE i.community_id=%s" in query
    assert params == (COMMUNITY, None, None, 0, 2)

    cases = Cursor([{"id": 5, "status": "open"}])
    monkeypatch.setattr(database, "get_connection", connection(cases))

    store.list_cases(COMMUNITY, status="open")

    query, params = cases.executed[0]
    assert "FROM invoice_queries WHERE community_id=%s" in query
    assert params == (COMMUNITY, "open", "open", 0, 51)

    payments = Cursor([{"id": 9, "match_decision": "matched"}])
    monkeypatch.setattr(database, "get_connection", connection(payments))

    store.list_payments(COMMUNITY, status="matched")

    query, params = payments.executed[0]
    assert "FROM bank_statement_entries WHERE community_id=%s" in query
    assert "match_decision=%s" in query
    assert params == (COMMUNITY, "matched", "matched", 0, 51)


def test_retry_reads_only_failed_runs_of_the_own_tenant(monkeypatch):
    schedule = {"territory": "tenant-a", "enabled": True, "max_attempts": 2}
    cursor = Cursor([schedule])
    monkeypatch.setattr(database, "get_connection", connection(cursor))

    assert store.get_ingestion_retry(COMMUNITY, 7) == schedule

    query, params = cursor.executed[0]
    assert "JOIN sdat_ingestion_schedules s ON s.territory=r.territory" in query
    assert "r.id=%s AND r.status='failure' AND r.territory=b.city_id" in query
    assert "owner.city_id=r.territory" in query
    assert params == (COMMUNITY, 7)

    foreign = Cursor([])
    monkeypatch.setattr(database, "get_connection", connection(foreign))

    assert store.get_ingestion_retry(COMMUNITY, 99) is None


def test_retry_claim_holds_the_key_only_for_an_eligible_failure(monkeypatch):
    schedule = {"territory": "tenant-a", "enabled": True, "max_attempts": 2}
    cursor = Cursor([{"idempotency_key": "key-7"}, schedule])
    monkeypatch.setattr(database, "get_connection", connection(cursor))

    assert store.claim_ingestion_retry(COMMUNITY, 7, "key-7") == {"schedule": schedule}

    assert "DELETE FROM operator_action_idempotency" in cursor.executed[0][0]
    assert "INSERT INTO operator_action_idempotency" in cursor.executed[1][0]
    eligibility_query, eligibility_params = cursor.executed[2]
    assert "r.status='failure' AND r.territory=b.city_id" in eligibility_query
    assert eligibility_params == (COMMUNITY, 7)


def test_retry_claim_releases_the_key_for_a_pending_job(monkeypatch):
    cursor = Cursor([{"idempotency_key": "key-7"}, None])
    monkeypatch.setattr(database, "get_connection", connection(cursor))

    assert store.claim_ingestion_retry(COMMUNITY, 7, "key-7") is None

    release_query, release_params = cursor.executed[3]
    assert "DELETE FROM operator_action_idempotency" in release_query
    assert "created_at" not in release_query
    assert release_params == (COMMUNITY, "metering.retry:7", "key-7")


def test_retry_claim_reports_pending_while_the_first_attempt_is_in_flight(monkeypatch):
    cursor = Cursor([None, None])
    monkeypatch.setattr(database, "get_connection", connection(cursor))

    assert store.claim_ingestion_retry(COMMUNITY, 7, "key-7") == {"pending": True}
    assert (
        "SELECT response,request_hash FROM operator_action_idempotency"
        in (cursor.executed[2][0])
    )


def test_replayed_retry_key_replays_the_completed_response(monkeypatch):
    stored = {
        "response": json.dumps({"status": "success", "territory": "tenant-a"}),
        "request_hash": store.request_hash({"job_id": 7}),
    }
    cursor = Cursor([None, stored])
    monkeypatch.setattr(database, "get_connection", connection(cursor))

    claim = store.claim_ingestion_retry(COMMUNITY, 7, "key-7")

    assert claim == {
        "replay": {"status": "success", "territory": "tenant-a", "replayed": True}
    }


def test_reused_retry_key_with_a_different_payload_conflicts(monkeypatch):
    cursor = Cursor([None, {"response": None, "request_hash": "a" * 64}])
    monkeypatch.setattr(database, "get_connection", connection(cursor))

    with pytest.raises(ValueError, match="already used for another request"):
        store.claim_ingestion_retry(COMMUNITY, 7, "key-7")


def _payment_entry(**overrides):
    entry = {
        "id": 12,
        "match_decision": "unmatched",
        "currency": "CHF",
        "is_reversal": False,
        "amount": Decimal("42.50"),
        "payment_reference": "BANK-1",
        "booking_date": date(2026, 9, 1),
    }
    return {**entry, **overrides}


def test_confirm_payment_locks_and_matches_only_inside_the_community(monkeypatch):
    cursor = Cursor(
        [
            None,
            _payment_entry(),
            {"id": 3, "gross_chf": Decimal("42.50"), "lifecycle_state": "delivered"},
            {"id": 77},
        ]
    )
    monkeypatch.setattr(database, "get_connection", connection(cursor))

    result = store.confirm_payment(12, 3, COMMUNITY, "operator-a", "key-12")

    event_id = event_id_for("payment.match.confirmed", "transition:77")
    assert result == {
        "id": 12,
        "match_decision": "matched",
        "event_id": event_id,
        "replayed": False,
    }
    entry_query, entry_params = cursor.executed[2]
    assert (
        "FROM bank_statement_entries WHERE id=%s AND community_id=%s FOR UPDATE"
        in entry_query
    )
    assert entry_params == (12, COMMUNITY)
    invoice_query_sql, invoice_params = cursor.executed[3]
    assert "i.id=%s AND i.community_id=%s FOR UPDATE" in invoice_query_sql
    assert invoice_params == (3, COMMUNITY)
    lifecycle_params = next(
        params
        for query, params in cursor.executed
        if "INSERT INTO invoice_lifecycle_events" in query
    )
    assert lifecycle_params == (
        3,
        COMMUNITY,
        "operator-a",
        "BANK-1",
        date(2026, 9, 1),
        "operator:key-12",
    )
    assert "'paid','delivered','paid'" in next(
        query
        for query, params in cursor.executed
        if "INSERT INTO invoice_lifecycle_events" in query
    )
    event_params = next(
        params
        for query, params in cursor.executed
        if "INSERT INTO operator_events" in query
    )
    assert event_params[:4] == (event_id, "payment.match.confirmed", "12", COMMUNITY)
    assert event_params[4].adapted == {"status": "matched"}
    delivery_params = next(
        params
        for query, params in cursor.executed
        if "INSERT INTO operator_webhook_deliveries" in query
    )
    assert delivery_params == (event_id, COMMUNITY, "payments.read", "payments.read")
    remember_query, remember_params = cursor.executed[-1]
    assert "INSERT INTO operator_action_idempotency" in remember_query
    assert remember_params[:3] == (COMMUNITY, "payment.confirm:12", "key-12")
    assert remember_params[3] == store.request_hash({"invoice_id": 3})


def test_confirm_payment_never_matches_a_foreign_community_entry(monkeypatch):
    cursor = Cursor([None, None])
    monkeypatch.setattr(database, "get_connection", connection(cursor))

    assert store.confirm_payment(12, 3, COMMUNITY, "operator-a", "key-12") is None

    entry_query, entry_params = cursor.executed[2]
    assert "community_id=%s" in entry_query
    assert entry_params == (12, COMMUNITY)
    assert len(cursor.executed) == 3


def test_confirm_payment_ignores_an_already_decided_entry(monkeypatch):
    cursor = Cursor([None, _payment_entry(match_decision="matched")])
    monkeypatch.setattr(database, "get_connection", connection(cursor))

    assert store.confirm_payment(12, 3, COMMUNITY, "operator-a", "key-12") is None
    assert len(cursor.executed) == 3


def test_confirm_payment_refuses_an_amount_that_misses_the_invoice(monkeypatch):
    cursor = Cursor(
        [
            None,
            _payment_entry(amount=Decimal("40.00")),
            {"id": 3, "gross_chf": Decimal("42.50"), "lifecycle_state": "delivered"},
        ]
    )
    monkeypatch.setattr(database, "get_connection", connection(cursor))

    with pytest.raises(billing_lifecycle.InvoiceLifecycleError):
        store.confirm_payment(12, 3, COMMUNITY, "operator-a", "key-12")
    assert not any(
        "UPDATE bank_statement_entries" in query for query, _ in cursor.executed
    )


def test_reused_confirm_key_with_a_different_payload_conflicts(monkeypatch):
    cursor = Cursor([{"response": None, "request_hash": "a" * 64}])
    monkeypatch.setattr(database, "get_connection", connection(cursor))

    with pytest.raises(ValueError, match="already used for another request"):
        store.confirm_payment(12, 3, COMMUNITY, "operator-a", "key-12")


def test_record_invoice_payment_enqueues_the_signed_paid_event(monkeypatch):
    from store import billing

    cursor = _LifecycleCursor(ones=[_issued_invoice(), {"new_state": "delivered"}])
    monkeypatch.setattr(database, "get_connection", _connection(cursor))

    billing.record_invoice_payment(
        42, COMMUNITY, "admin-building", date(2026, 9, 1), "BANK-42"
    )

    event_id = event_id_for("invoice.paid", "42")
    assert event_id == str(uuid.uuid5(uuid.NAMESPACE_URL, "openleg:invoice.paid:42"))
    event_query, event_params = next(
        (query, params)
        for query, params in cursor.executed
        if "INSERT INTO operator_events" in query
    )
    assert "ON CONFLICT (event_id) DO NOTHING" in event_query
    assert event_params[:4] == (event_id, "invoice.paid", "42", COMMUNITY)
    assert event_params[4].adapted == {"status": "paid"}
    delivery_query, delivery_params = next(
        (query, params)
        for query, params in cursor.executed
        if "INSERT INTO operator_webhook_deliveries" in query
    )
    assert "capabilities ? %s" in delivery_query
    assert delivery_params == (event_id, COMMUNITY, "billing.read", "billing.read")
