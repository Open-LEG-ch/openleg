# SPDX-License-Identifier: AGPL-3.0-or-later
"""Public contract for ISO 20022 payment reconciliation."""

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
