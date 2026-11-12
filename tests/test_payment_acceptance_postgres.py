# SPDX-License-Identifier: AGPL-3.0-or-later
"""Payment acceptance through authenticated HTTP and real PostgreSQL."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier

import pytest
from flask import Flask

import database as db
import operator_api
from store import billing
from tests import test_store_billing_postgres
from tests.test_store_billing_postgres import (
    COMMUNITY,
    _insert_issued_invoice,
)

billing_database = test_store_billing_postgres.billing_database

pytestmark = pytest.mark.integration


@pytest.fixture
def payment_client(billing_database):
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
    return app


def _invoice(invoice_number="LEG-PAYMENT-1"):
    invoice_id = _insert_issued_invoice(invoice_number)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO invoice_lifecycle_events
               (invoice_id,community_id,actor_id,event_type,previous_state,
                new_state,idempotency_key)
               VALUES (%s,%s,'operator-a','delivered','issued','delivered','delivery')""",
            (invoice_id, COMMUNITY),
        )
    return invoice_id


def _import(*, direction="CRDT", reference="unknown reference", **overrides):
    entry = {
        "entry_reference": "bank-entry-1",
        "booking_date": date(2026, 11, 4),
        "amount": Decimal("9.67"),
        "currency": "CHF",
        "payment_reference": reference,
        "is_reversal": False,
        "credit_debit_indicator": direction,
        "booking_status": "BOOK",
        "amount_basis": "entry",
        **overrides,
    }
    result = db.reconcile_bank_statement(
        community_id=COMMUNITY,
        actor_id="operator-a",
        source_name="fictional.xml",
        message_type="camt.053",
        statement_reference="fictional-1",
        fingerprint="1" * 64,
        entries=[entry],
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM bank_statement_entries")
        entry_id = cur.fetchone()["id"]
    return entry_id, result


def _confirm(app, entry_id, invoice_id, key="confirm-1"):
    with app.test_client() as client:
        return client.post(
            f"/api/operator/v1/communities/{COMMUNITY}/payments/matches/{entry_id}/confirm",
            headers={
                "Authorization": "Bearer fictional-token",
                "Idempotency-Key": key,
            },
            json={"invoice_id": invoice_id},
        )


def test_reviewed_debit_cannot_mark_invoice_paid(payment_client):
    invoice_id = _invoice()
    entry_id, result = _import(direction="DBIT")
    assert result["entries"][0]["match_decision"] == "unmatched"

    response = _confirm(payment_client, entry_id, invoice_id)

    assert response.status_code == 409
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT match_decision FROM bank_statement_entries")
        assert cur.fetchone()["match_decision"] == "unmatched"
        cur.execute(
            "SELECT COUNT(*) AS n FROM invoice_lifecycle_events WHERE event_type='paid'"
        )
        assert cur.fetchone()["n"] == 0

        cur.execute("SELECT COUNT(*) AS n FROM operator_events")
        assert cur.fetchone()["n"] == 0
        cur.execute("SELECT COUNT(*) AS n FROM operator_action_idempotency")
        assert cur.fetchone()["n"] == 0


@pytest.mark.parametrize("adapter", ["import", "review"])
@pytest.mark.parametrize(
    "overrides,accepted",
    [
        ({}, True),
        ({"direction": "DBIT"}, False),
        ({"is_reversal": True}, False),
        ({"currency": "EUR"}, False),
        ({"amount": Decimal("9.66")}, False),
        ({"amount": Decimal("9.68")}, False),
        ({"cancelled": True}, False),
    ],
)
def test_both_adapters_apply_the_same_acceptance_rules(
    payment_client, adapter, overrides, accepted
):
    invoice_id = _invoice()
    overrides = dict(overrides)
    if overrides.pop("cancelled", False):
        billing.cancel_invoice(
            invoice_id, COMMUNITY, "operator-a", "Fictional cancellation"
        )
    reference = "LEG-PAYMENT-1" if adapter == "import" else "unmatched reference"
    entry_id, result = _import(reference=reference, **overrides)
    if adapter == "review":
        # A reviewed candidate cannot waive eligibility, including a reversal
        # labelled mismatch by an earlier classifier.
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE bank_statement_entries SET match_decision='mismatch'")
        response = _confirm(payment_client, entry_id, invoice_id)
        assert response.status_code == (200 if accepted else 409)
    else:
        assert (result["entries"][0]["match_decision"] == "matched") is accepted

    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM invoice_lifecycle_events WHERE event_type='paid'"
        )
        assert cur.fetchone()["n"] == int(accepted)
        cur.execute(
            "SELECT policy_snapshot,line_items_snapshot FROM invoices WHERE id=%s",
            (invoice_id,),
        )
        row = cur.fetchone()
        assert row["policy_snapshot"] == {"internal_price_rp_kwh": "11.00"}
        assert row["line_items_snapshot"] == [
            {"kind": "consumer_charge", "amount_chf": "9.67"}
        ]
        cur.execute(
            """SELECT e.event_type,c.name,e.payload FROM operator_events e
               JOIN operator_webhook_deliveries d USING(event_id)
               JOIN operator_api_clients c ON c.id=d.client_id
               WHERE e.event_type IN ('invoice.paid','payment.match.confirmed')"""
        )
        deliveries = cur.fetchall()
        expected = {("invoice.paid", "billing.read")} if accepted else set()
        if accepted and adapter == "review":
            expected.add(("payment.match.confirmed", "payments.read"))
            assert (
                response.get_json()["event_id"] == _events("payment.match.confirmed")[0]
            )
        assert {(r["event_type"], r["name"]) for r in deliveries} == expected


def _events(event_type):
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT event_id FROM operator_events WHERE event_type=%s", (event_type,)
        )
        return [row["event_id"] for row in cur.fetchall()]


def test_review_replay_is_request_bound_and_cannot_book_twice(payment_client):
    invoice_id = _invoice()
    entry_id, _ = _import()
    first = _confirm(payment_client, entry_id, invoice_id)
    replay = _confirm(payment_client, entry_id, invoice_id)
    conflict = _confirm(payment_client, entry_id, invoice_id + 1)
    assert first.status_code == replay.status_code == 200
    assert replay.get_json() == {**first.get_json(), "replayed": True}
    assert conflict.status_code == 409
    assert len(_events("invoice.paid")) == len(_events("payment.match.confirmed")) == 1


@pytest.mark.parametrize("same_key", [False, True])
def test_concurrent_review_cannot_book_twice(payment_client, same_key):
    invoice_id = _invoice()
    entry_id, _ = _import()
    barrier = Barrier(2)

    def confirm(index):
        barrier.wait(timeout=5)
        return _confirm(
            payment_client,
            entry_id,
            invoice_id,
            "concurrent" if same_key else f"confirm-{index}",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(confirm, range(2)))
    assert sorted(r.status_code for r in responses) == (
        [200, 200] if same_key else [200, 404]
    )
    assert len(_events("invoice.paid")) == len(_events("payment.match.confirmed")) == 1


@pytest.mark.parametrize("event_type", ["invoice.paid", "payment.match.confirmed"])
def test_failure_of_either_notification_rolls_back_every_write(
    payment_client, monkeypatch, event_type
):
    invoice_id = _invoice()
    entry_id, _ = _import()
    original = billing.enqueue_event

    def fail(cur, kind, *args, **kwargs):
        result = original(cur, kind, *args, **kwargs)
        if kind == event_type:
            raise RuntimeError("deliberate outbox failure")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(billing, "enqueue_event", fail)
        with pytest.raises(RuntimeError, match="deliberate outbox failure"):
            _confirm(payment_client, entry_id, invoice_id)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT match_decision,invoice_id FROM bank_statement_entries")
        assert dict(cur.fetchone()) == {
            "match_decision": "unmatched",
            "invoice_id": None,
        }
        for table in (
            "operator_events",
            "operator_webhook_deliveries",
            "operator_action_idempotency",
        ):
            cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
            assert cur.fetchone()["n"] == 0
        cur.execute(
            "SELECT COUNT(*) AS n FROM invoice_lifecycle_events WHERE event_type='paid'"
        )
        assert cur.fetchone()["n"] == 0
    assert _confirm(payment_client, entry_id, invoice_id).status_code == 200


def test_review_rejects_foreign_community_and_foreign_invoice(payment_client):
    invoice_id = _invoice()
    entry_id, _ = _import()
    with payment_client.test_client() as client:
        response = client.post(
            f"/api/operator/v1/communities/foreign/payments/matches/{entry_id}/confirm",
            headers={
                "Authorization": "Bearer fictional-token",
                "Idempotency-Key": "foreign",
            },
            json={"invoice_id": invoice_id},
        )
    assert response.status_code == 404
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO invoices(community_id,participant_id,invoice_number,status,
                   policy_snapshot,provenance_snapshot,line_items_snapshot,
                   net_chf,vat_rate_pct,vat_chf,gross_chf,issue_date,due_date)
               SELECT 'foreign',participant_id,'FOREIGN-1',status,
                      policy_snapshot,provenance_snapshot,line_items_snapshot,
                      net_chf,vat_rate_pct,vat_chf,gross_chf,issue_date,due_date
               FROM invoices WHERE id=%s RETURNING id""",
            (invoice_id,),
        )
        foreign_invoice_id = cur.fetchone()["id"]
    assert _confirm(payment_client, entry_id, foreign_invoice_id).status_code == 409
    assert _events("invoice.paid") == _events("payment.match.confirmed") == []


@pytest.mark.parametrize("adapter", ["import", "review"])
def test_lifecycle_is_rechecked_after_waiting_for_invoice_lock(payment_client, adapter):
    import time

    invoice_id = _invoice()
    if adapter == "review":
        entry_id, _ = _import()
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM invoices WHERE id=%s FOR UPDATE", (invoice_id,))
        # A second connection starts acceptance while this transaction owns the
        # invoice lock, then must observe its cancellation once the lock clears.
        with ThreadPoolExecutor(max_workers=1) as executor:
            if adapter == "review":
                future = executor.submit(_confirm, payment_client, entry_id, invoice_id)
            else:
                future = executor.submit(_import, reference="LEG-PAYMENT-1")
            deadline = time.monotonic() + 10
            waiting = False
            try:
                while time.monotonic() < deadline:
                    cur.execute("SELECT pg_stat_clear_snapshot()")
                    cur.execute(
                        """SELECT EXISTS(SELECT 1 FROM pg_stat_activity
                           WHERE datname=current_database() AND pid<>pg_backend_pid()
                             AND wait_event_type='Lock') AS waiting"""
                    )
                    waiting = cur.fetchone()["waiting"]
                    if waiting:
                        break
                    time.sleep(0.02)
                assert waiting, "payment did not wait for the invoice lock"
                cur.execute(
                    """INSERT INTO invoice_lifecycle_events
                       (invoice_id,community_id,actor_id,event_type,previous_state,new_state,
                        reason,idempotency_key)
                       VALUES (%s,%s,'operator-a','cancelled','delivered','cancelled',
                               'Fictional concurrent cancellation','cancel')""",
                    (invoice_id, COMMUNITY),
                )
            finally:
                conn.commit()
            result = future.result(timeout=10)
    if adapter == "review":
        assert result.status_code == 409
    else:
        assert result[1]["entries"][0]["match_decision"] == "mismatch"
    assert _events("invoice.paid") == _events("payment.match.confirmed") == []


def test_duplicate_import_cannot_emit_a_second_paid_event(payment_client):
    _invoice()
    _, first = _import(reference="LEG-PAYMENT-1")
    _, replay = _import(reference="LEG-PAYMENT-1")
    assert first["duplicate"] is False
    assert replay["duplicate"] is True
    assert first["statement_import_id"] == replay["statement_import_id"]
    assert len(_events("invoice.paid")) == 1
    assert _events("payment.match.confirmed") == []


def test_two_reviewed_entries_cannot_pay_one_invoice_twice(payment_client):
    invoice_id = _invoice()
    first_id, _ = _import()
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO bank_statement_entries(
                   statement_import_id,community_id,entry_reference,booking_date,
                   amount,currency,payment_reference,is_reversal,credit_debit_indicator,match_decision,booking_status,amount_basis)
               SELECT statement_import_id,community_id,'bank-entry-2',booking_date,
                      amount,currency,payment_reference,is_reversal,credit_debit_indicator,match_decision,booking_status,amount_basis
               FROM bank_statement_entries WHERE id=%s RETURNING id""",
            (first_id,),
        )
        second_id = cur.fetchone()["id"]
    barrier = Barrier(2)

    def confirm(entry_id):
        barrier.wait(timeout=5)
        return _confirm(payment_client, entry_id, invoice_id, f"entry-{entry_id}")

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(confirm, (first_id, second_id)))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert len(_events("invoice.paid")) == len(_events("payment.match.confirmed")) == 1
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT match_decision FROM bank_statement_entries ORDER BY id")
        assert sorted(r["match_decision"] for r in cur.fetchall()) == [
            "matched",
            "unmatched",
        ]


@pytest.mark.parametrize("adapter", ["import", "review"])
@pytest.mark.parametrize("status", ["BOOK", "PDNG", "INFO", "", "FUTR"])
def test_booking_status_survives_import_and_gates_payment(
    payment_client, adapter, status
):
    import payment_reconciliation
    from sandbox_payment_fixtures import fixture

    invoice_id = _invoice()
    xml = fixture(
        {"id": invoice_id, "gross_chf": "9.67", "invoice_number": "LEG-PAYMENT-1"},
        "exact",
        "054",
    )
    # Older synthetic fixtures omitted Sts. Strip it before selecting a case.
    xml = xml.replace(b"<Sts><Cd>BOOK</Cd></Sts>", b"")
    status_xml = f"<Sts><Cd>{status}</Cd></Sts>".encode() if status else b""
    xml = xml.replace(b"<BookgDt>", status_xml + b"<BookgDt>")
    if adapter == "review":
        xml = xml.replace(b"LEG-PAYMENT-1", b"unmatched reference")
    result = payment_reconciliation.import_statement(
        COMMUNITY, "operator-a", "status.xml", xml, db
    )
    rows = db.list_bank_statement_entries(COMMUNITY)
    assert rows[0]["booking_status"] == (status or None)
    if adapter == "review":
        response = _confirm(payment_client, rows[0]["id"], invoice_id)
        assert response.status_code == (200 if status == "BOOK" else 409)
    else:
        assert (result["entries"][0]["match_decision"] == "matched") is (
            status == "BOOK"
        )
    rows = db.list_bank_statement_entries(COMMUNITY)
    assert (rows[0]["match_decision"] == "matched") is (status == "BOOK")
    assert len(_events("invoice.paid")) == int(status == "BOOK")
    assert len(_events("payment.match.confirmed")) == int(
        status == "BOOK" and adapter == "review"
    )
    invoice = db.get_invoices_for_participant("member-building")[0]
    assert invoice["lifecycle_state"] == ("paid" if status == "BOOK" else "delivered")
    assert invoice["gross_chf"] == Decimal("9.67")


def test_legacy_rows_stay_unknown_after_idempotent_schema_upgrade(payment_client):
    invoice_id = _invoice()
    entry_id, _ = _import(booking_status=None)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("ALTER TABLE bank_statement_entries DROP COLUMN booking_status")
    db.create_tables()
    db.create_tables()
    assert db.list_bank_statement_entries(COMMUNITY)[0]["booking_status"] is None
    assert _confirm(payment_client, entry_id, invoice_id).status_code == 409
    assert _events("invoice.paid") == _events("payment.match.confirmed") == []


@pytest.mark.parametrize("adapter", ["import", "review"])
def test_booked_eur_credit_cannot_use_instructed_chf_amount(payment_client, adapter):
    import payment_reconciliation
    from sandbox_payment_fixtures import fixture

    invoice_id = _invoice()
    xml = fixture(
        {"id": invoice_id, "gross_chf": "9.67", "invoice_number": "LEG-PAYMENT-1"},
        "exact",
        "054",
    )
    xml = xml.replace(b'<Amt Ccy="CHF">9.67</Amt>', b'<Amt Ccy="EUR">8.00</Amt>')
    xml = xml.replace(
        b"</Refs>",
        b'</Refs><AmtDtls><InstdAmt><Amt Ccy="CHF">9.67</Amt></InstdAmt><TxAmt><Amt Ccy="EUR">8.00</Amt></TxAmt></AmtDtls>',
    )
    result = payment_reconciliation.import_statement(
        COMMUNITY, "operator-a", "fx.xml", xml, db
    )
    row = db.list_bank_statement_entries(COMMUNITY)[0]
    assert row["amount_basis"] == "entry"
    assert row["amount"] == Decimal("8.00")
    assert row["currency"] == "EUR"
    assert result["entries"][0]["match_decision"] == "mismatch"
    if adapter == "review":
        assert _confirm(payment_client, row["id"], invoice_id).status_code == 409
    assert _events("invoice.paid") == _events("payment.match.confirmed") == []


def test_legacy_amounts_stay_untrusted_after_schema_upgrade(payment_client):
    invoice_id = _invoice()
    entry_id, _ = _import(amount_basis=None)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("ALTER TABLE bank_statement_entries DROP COLUMN amount_basis")
    db.create_tables()
    db.create_tables()
    row = db.list_bank_statement_entries(COMMUNITY)[0]
    assert row["amount_basis"] is None
    assert _confirm(payment_client, entry_id, invoice_id).status_code == 409
    assert _events("invoice.paid") == _events("payment.match.confirmed") == []


def test_reconciled_chf_batch_pays_each_invoice_once(payment_client):
    from copy import deepcopy
    from xml.etree import ElementTree

    import payment_reconciliation
    from sandbox_payment_fixtures import fixture

    _invoice()
    _invoice("LEG-PAYMENT-2")
    xml = fixture(
        {"id": 1, "gross_chf": "9.67", "invoice_number": "LEG-PAYMENT-1"},
        "exact",
        "054",
    )
    root = ElementTree.fromstring(xml)
    ns = "{urn:iso:std:iso:20022:tech:xsd:camt.054.001.08}"
    entry = root.find(".//" + ns + "Ntry")
    entry.find(ns + "Amt").text = "19.34"
    details = entry.find(ns + "NtryDtls")
    transaction = details.find(ns + "TxDtls")
    amount = ElementTree.Element(ns + "Amt", {"Ccy": "CHF"})
    amount.text = "9.67"
    transaction.insert(1, amount)
    second = deepcopy(transaction)
    second.find(".//" + ns + "EndToEndId").text = "LEG-PAYMENT-2"
    second.find(".//" + ns + "Ustrd").text = "LEG-PAYMENT-2"
    details.append(second)
    content = ElementTree.tostring(root)
    first = payment_reconciliation.import_statement(
        COMMUNITY, "operator-a", "batch.xml", content, db
    )
    replay = payment_reconciliation.import_statement(
        COMMUNITY, "operator-a", "batch.xml", content, db
    )
    assert [row["match_decision"] for row in first["entries"]] == ["matched", "matched"]
    assert [row["amount"] for row in first["entries"]] == [
        Decimal("9.67"),
        Decimal("9.67"),
    ]
    assert {
        row["amount_basis"] for row in db.list_bank_statement_entries(COMMUNITY)
    } == {"reconciled_batch"}
    assert replay["duplicate"] is True
    assert len(_events("invoice.paid")) == 2


@pytest.mark.parametrize(
    "batch,currency,direct_amount,charges,accepted",
    [
        ("<NbOfTxs>1</NbOfTxs><CdtDbtInd>DBIT</CdtDbtInd>", None, True, "", False),
        ("<NbOfTxs>1</NbOfTxs>", None, False, "", False),
        ("<NbOfTxs>1</NbOfTxs>", None, True, "<Chrgs/>", False),
        ('<NbOfTxs>1</NbOfTxs><TtlAmt Ccy="CHF">8.00</TtlAmt>', None, True, "", False),
        ("", "EUR", False, "", False),
        (
            '<NbOfTxs>1</NbOfTxs><TtlAmt Ccy="CHF">9.67</TtlAmt><CdtDbtInd>CRDT</CdtDbtInd>',
            "CHF",
            True,
            "",
            True,
        ),
    ],
)
def test_contradictory_bank_metadata_cannot_create_a_payment(
    payment_client, batch, currency, direct_amount, charges, accepted
):
    import payment_reconciliation
    from sandbox_payment_fixtures import fixture

    _invoice()
    xml = fixture(
        {"id": 1, "gross_chf": "9.67", "invoice_number": "LEG-PAYMENT-1"},
        "exact",
        "054",
    )
    if batch:
        xml = xml.replace(
            b"<NtryDtls>", b"<NtryDtls><Btch>" + batch.encode() + b"</Btch>"
        )
    if currency:
        xml = xml.replace(b"</Acct>", f"<Ccy>{currency}</Ccy></Acct>".encode())
    if direct_amount:
        xml = xml.replace(b"</Refs>", b'</Refs><Amt Ccy="CHF">9.67</Amt>')
    if charges:
        xml = xml.replace(b"<NtryDtls>", charges.encode() + b"<NtryDtls>")
    if accepted:
        result = payment_reconciliation.import_statement(
            COMMUNITY, "operator-a", "metadata.xml", xml, db
        )
        assert result["entries"][0]["match_decision"] == "matched"
        assert len(_events("invoice.paid")) == 1
    else:
        with pytest.raises(payment_reconciliation.StatementError):
            payment_reconciliation.import_statement(
                COMMUNITY, "operator-a", "metadata.xml", xml, db
            )
        assert db.list_bank_statement_entries(COMMUNITY) == []
        assert _events("invoice.paid") == _events("payment.match.confirmed") == []
        invoice = db.get_invoices_for_participant("member-building")[0]
        assert invoice["lifecycle_state"] == "delivered"
        assert invoice["gross_chf"] == Decimal("9.67")
