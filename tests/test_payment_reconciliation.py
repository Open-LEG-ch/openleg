# SPDX-License-Identifier: AGPL-3.0-or-later
"""Public contract for ISO 20022 payment reconciliation."""

from contextlib import contextmanager
from dataclasses import asdict
from datetime import date
from decimal import Decimal

import pytest

import payment_reconciliation

CAMT_053 = b"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">
  <BkToCstmrStmt><Stmt><Id>statement-1</Id><Ntry>
    <Amt Ccy="CHF">120.50</Amt><CdtDbtInd>CRDT</CdtDbtInd><BookgDt><Dt>2026-09-14</Dt></BookgDt>
    <NtryDtls><TxDtls><Refs><AcctSvcrRef>bank-entry-1</AcctSvcrRef><EndToEndId>LEG-2026-000042</EndToEndId></Refs>
    <RmtInf><Ustrd>LEG-2026-000042</Ustrd></RmtInf></TxDtls></NtryDtls>
  </Ntry></Stmt></BkToCstmrStmt>
</Document>"""


def test_parse_supported_statement_returns_canonical_credit():
    statement = payment_reconciliation.parse_statement(CAMT_053)

    assert statement.message_type == "camt.053"
    assert statement.statement_reference == "statement-1"
    assert statement.entries[0].amount == Decimal("120.50")
    assert statement.entries[0].currency == "CHF"
    assert statement.entries[0].booking_date == date(2026, 9, 14)
    assert statement.entries[0].payment_reference == "LEG-2026-000042"
    assert statement.entries[0].entry_reference == "bank-entry-1"
    assert statement.entries[0].is_reversal is False
    assert statement.entries[0].credit_debit_indicator == "CRDT"


def test_parse_rejects_non_camt_and_malformed_money():
    with pytest.raises(payment_reconciliation.StatementError):
        payment_reconciliation.parse_statement(b"<Document><Pain/></Document>")

    with pytest.raises(payment_reconciliation.StatementError):
        payment_reconciliation.parse_statement(CAMT_053.replace(b"120.50", b"NaN"))


def test_import_passes_provenance_and_fingerprint_to_store():
    calls = []

    class Store:
        def reconcile_bank_statement(self, **kwargs):
            calls.append(kwargs)
            return {"duplicate": False, "entries": []}

    result = payment_reconciliation.import_statement(
        "community-a", "admin-a", "konto.xml", CAMT_053, Store()
    )

    assert result["duplicate"] is False
    assert calls[0]["community_id"] == "community-a"
    assert calls[0]["actor_id"] == "admin-a"
    assert calls[0]["source_name"] == "konto.xml"
    assert calls[0]["message_type"] == "camt.053"
    assert len(calls[0]["fingerprint"]) == 64
    assert calls[0]["entries"][0]["payment_reference"] == "LEG-2026-000042"


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"amount": Decimal("50.00")}, "split_payment"),
        ({"amount": Decimal("130.00")}, "overpayment"),
        ({"currency": "EUR"}, "mismatch"),
        ({"is_reversal": True}, "reversal"),
    ],
)
def test_non_exact_payments_remain_reviewable(overrides, expected):
    entry = {
        "payment_reference": "LEG-2026-000042",
        "amount": Decimal("120.50"),
        "currency": "CHF",
        "is_reversal": False,
        "credit_debit_indicator": "CRDT",
        **overrides,
    }
    invoices = [
        {
            "id": 42,
            "invoice_number": "LEG-2026-000042",
            "gross_chf": Decimal("120.50"),
            "lifecycle_state": "delivered",
        }
    ]

    assert payment_reconciliation.match_payment(entry, invoices).decision == expected


def test_only_a_unique_reference_amount_currency_and_delivered_state_matches():
    entry = {
        "payment_reference": "LEG 2026 000042",
        "amount": Decimal("120.50"),
        "currency": "CHF",
        "is_reversal": False,
        "credit_debit_indicator": "CRDT",
    }
    invoice = {
        "id": 42,
        "invoice_number": "LEG-2026-000042",
        "gross_chf": Decimal("120.50"),
        "lifecycle_state": "delivered",
    }

    assert payment_reconciliation.match_payment(entry, [invoice]) == (
        payment_reconciliation.MatchDecision("matched", 42)
    )
    assert payment_reconciliation.match_payment(entry, []).decision == "unmatched"
    assert (
        payment_reconciliation.match_payment(
            entry, [{**invoice, "lifecycle_state": "paid"}]
        ).decision
        == "mismatch"
    )
    duplicate = {**invoice, "id": 43, "invoice_number": "LEG2026000042"}
    assert (
        payment_reconciliation.match_payment(entry, [invoice, duplicate]).decision
        == "ambiguous"
    )


def test_statement_import_requires_confirmed_community_admin(monkeypatch):
    import dashboard

    monkeypatch.setattr(dashboard, "_require_confirmed_admin", lambda *_: None)
    called = []
    monkeypatch.setattr(
        dashboard.payment_reconciliation,
        "import_statement",
        lambda *_: called.append(True),
    )

    result = dashboard.leg_import_bank_statement(
        "community-a", "outsider", "konto.xml", CAMT_053
    )

    assert result == {"error": "Kein Zugriff."}
    assert called == []


def test_confirmed_admin_imports_into_the_exact_community(monkeypatch):
    import dashboard

    monkeypatch.setattr(
        dashboard, "_require_confirmed_admin", lambda *_: {"status": "confirmed"}
    )
    called = []

    def import_statement(*args):
        called.append(args)
        return {"duplicate": False, "entries": [{"match_decision": "matched"}]}

    monkeypatch.setattr(
        dashboard.payment_reconciliation, "import_statement", import_statement
    )

    result = dashboard.leg_import_bank_statement(
        "community-a", "admin-a", "konto.xml", CAMT_053
    )

    assert called == [("community-a", "admin-a", "konto.xml", CAMT_053, dashboard.db)]
    assert result["error"] is None


CAMT_054_REVERSAL = b"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.054.001.08">
  <BkToCstmrDbtCdtNtfctn><Ntfctn><Id>notification-1</Id><Ntry>
    <Amt Ccy="CHF">120.50</Amt><CdtDbtInd>DBIT</CdtDbtInd><RvslInd>true</RvslInd>
    <BookgDt><Dt>2026-09-15</Dt></BookgDt>
    <NtryDtls><TxDtls><Refs><AcctSvcrRef>bank-entry-9</AcctSvcrRef><EndToEndId>LEG-2026-000042</EndToEndId></Refs>
    <RmtInf><Ustrd>LEG-2026-000042</Ustrd></RmtInf></TxDtls></NtryDtls>
  </Ntry></Ntfctn></BkToCstmrDbtCdtNtfctn>
</Document>"""


class _ReconcileCursor:
    def __init__(self, ones=(), rows=()):
        self._ones = list(ones)
        self._rows = list(rows)
        self.executed = []
        self.rowcount = 1

    def execute(self, query, params=None):
        self.executed.append((" ".join(query.split()), params))

    def fetchone(self):
        return self._ones.pop(0) if self._ones else None

    def fetchall(self):
        return self._rows.pop(0) if self._rows else []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _connection(cursor):
    class Connection:
        def cursor(self):
            return cursor

    @contextmanager
    def factory():
        yield Connection()

    return factory


def _delivered_invoice():
    return {
        "id": 42,
        "invoice_number": "LEG-2026-000042",
        "gross_chf": Decimal("120.50"),
        "lifecycle_state": "delivered",
    }


def _bank_entry(**overrides):
    entry = {
        "entry_reference": "bank-entry-1",
        "booking_date": date(2026, 9, 14),
        "amount": Decimal("120.50"),
        "currency": "CHF",
        "payment_reference": "LEG-2026-000042",
        "is_reversal": False,
        "credit_debit_indicator": "CRDT",
    }
    entry.update(overrides)
    return entry


def _event_inserts(cursor):
    return [
        (query, params)
        for query, params in cursor.executed
        if "INSERT INTO invoice_lifecycle_events" in query
    ]


def _entry_insert_params(cursor):
    return [
        params
        for query, params in cursor.executed
        if "INSERT INTO bank_statement_entries" in query
    ]


def test_duplicate_statement_replay_returns_duplicate_without_second_paid_event(
    monkeypatch,
):
    import database
    from store import billing

    entries = [
        asdict(entry)
        for entry in payment_reconciliation.parse_statement(CAMT_053).entries
    ]
    cursor = _ReconcileCursor(
        ones=[{"id": 7}, None, {"id": 7}],
        rows=[
            [_delivered_invoice()],
            [{"match_decision": "matched"}],
            [{"match_decision": "matched"}],
        ],
    )
    monkeypatch.setattr(database, "get_connection", _connection(cursor))
    call = {
        "community_id": "community-a",
        "actor_id": "admin-a",
        "source_name": "konto.xml",
        "message_type": "camt.053",
        "statement_reference": "statement-1",
        "fingerprint": "f" * 64,
        "entries": entries,
    }

    first = billing.reconcile_bank_statement(**call)
    second = billing.reconcile_bank_statement(**call)

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["statement_import_id"] == 7
    assert any(
        "SELECT id FROM bank_statement_imports" in query and "fingerprint = %s" in query
        for query, _ in cursor.executed
    )
    assert len(_event_inserts(cursor)) == 1
    assert len(_entry_insert_params(cursor)) == 1


def test_other_community_invoice_is_never_a_match_candidate(monkeypatch):
    import database
    from store import billing

    cursor = _ReconcileCursor(ones=[{"id": 9}], rows=[[_delivered_invoice()], []])
    monkeypatch.setattr(database, "get_connection", _connection(cursor))

    result = billing.reconcile_bank_statement(
        community_id="community-a",
        actor_id="admin-a",
        source_name="konto.xml",
        message_type="camt.053",
        statement_reference="statement-1",
        fingerprint="a" * 64,
        entries=[
            _bank_entry(
                entry_reference="bank-entry-2",
                payment_reference="LEG-B-000077",
                amount=Decimal("80.00"),
            )
        ],
    )

    assert result["duplicate"] is False
    invoices_sql, invoices_params = next(
        (query, params)
        for query, params in cursor.executed
        if "FROM invoices i" in query
    )
    assert "WHERE i.community_id = %s" in invoices_sql
    assert "FOR UPDATE OF i" in invoices_sql
    assert invoices_params == ("community-a",)
    assert _event_inserts(cursor) == []
    entry_params = _entry_insert_params(cursor)[0]
    assert entry_params[2] is None
    assert entry_params[10] == "unmatched"


def test_camt054_reversal_parses_end_to_end_and_books_nothing(monkeypatch):
    import database
    from store import billing

    statement = payment_reconciliation.parse_statement(CAMT_054_REVERSAL)
    assert statement.message_type == "camt.054"
    assert statement.statement_reference == "notification-1"
    entry = asdict(statement.entries[0])
    assert entry["is_reversal"] is True
    assert entry["payment_reference"] == "LEG-2026-000042"
    assert entry["booking_date"] == date(2026, 9, 15)
    assert (
        payment_reconciliation.match_payment(entry, [_delivered_invoice()]).decision
        == "reversal"
    )

    cursor = _ReconcileCursor(ones=[{"id": 5}], rows=[[_delivered_invoice()], []])
    monkeypatch.setattr(database, "get_connection", _connection(cursor))
    result = billing.reconcile_bank_statement(
        community_id="community-a",
        actor_id="admin-a",
        source_name="benachrichtigung.xml",
        message_type=statement.message_type,
        statement_reference=statement.statement_reference,
        fingerprint="c" * 64,
        entries=[entry],
    )

    assert result["duplicate"] is False
    assert _event_inserts(cursor) == []
    assert _entry_insert_params(cursor)[0][10] == "reversal"


def test_replayed_entry_reference_does_not_append_a_second_paid_event(monkeypatch):
    import database
    from store import billing

    paid_invoice = {**_delivered_invoice(), "lifecycle_state": "paid"}
    cursor = _ReconcileCursor(
        ones=[{"id": 7}, {"id": 8}],
        rows=[
            [_delivered_invoice()],
            [{"match_decision": "matched"}],
            [paid_invoice],
            [{"match_decision": "mismatch"}],
        ],
    )
    monkeypatch.setattr(database, "get_connection", _connection(cursor))

    def reconcile(fingerprint):
        return billing.reconcile_bank_statement(
            community_id="community-a",
            actor_id="admin-a",
            source_name="konto.xml",
            message_type="camt.053",
            statement_reference="statement-1",
            fingerprint=fingerprint,
            entries=[_bank_entry()],
        )

    first = reconcile("b" * 64)
    second = reconcile("d" * 64)

    assert first["duplicate"] is False
    assert second["duplicate"] is False
    assert second["statement_import_id"] == 8
    event_sql, event_params = _event_inserts(cursor)[0]
    assert "ON CONFLICT (invoice_id, idempotency_key) DO NOTHING" in event_sql
    assert event_params[:4] == (42, "community-a", "admin-a", "paid")
    assert event_params[9] == "bank:7:bank-entry-1"
    assert len(_event_inserts(cursor)) == 1
    decisions = [params[10] for params in _entry_insert_params(cursor)]
    assert decisions == ["matched", "mismatch"]
