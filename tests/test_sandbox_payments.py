# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fictional bank imports and reviewed matches use the real payment commands."""

import io
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from tests import test_sandbox_http
from tests.test_sandbox_billing import COMMUNITY, _import, _policy, _prepare
from tests.test_sandbox_metering import _workspace_connection
from tests.test_sandbox_personas import _start, _switch

village = test_sandbox_http.village
_csrf = test_sandbox_http._csrf
pytestmark = pytest.mark.integration


def _issued(village, client):
    prefix, _ = _start(client, "operator")
    _policy(client, prefix)
    _import(client, prefix)
    assert _prepare(client, prefix).status_code == 200
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute("SELECT id FROM billing_periods")
        period = cur.fetchone()[0]
    _switch(client, prefix, "administrator")
    page = client.get(prefix + COMMUNITY + "/billing")
    assert (
        client.post(
            prefix + COMMUNITY + f"/billing/period/{period}/approve",
            data={"csrf_token": _csrf(page), "confirm_approval": "yes"},
        ).status_code
        == 302
    )
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute("SELECT participant_id,id FROM invoices")
        invoices = dict(cur.fetchall())
    for member in ("sonnwil-resident", "sonnwil-neighbour"):
        page = client.get(prefix + COMMUNITY + "/billing")
        assert (
            client.post(
                prefix + COMMUNITY + f"/billing/invoice/{invoices[member]}/deliver",
                data={"csrf_token": _csrf(page)},
            ).status_code
            == 302
        )
    return prefix, invoices


def _statement(client, prefix, fixture):
    page = client.get(prefix + COMMUNITY + "/billing")
    return client.post(
        prefix + COMMUNITY + "/billing/statements",
        data={
            "csrf_token": _csrf(page),
            "statement": (io.BytesIO(fixture.data), "simulation-camt.xml"),
        },
        content_type="multipart/form-data",
    )


def test_exact_import_and_reviewed_unknown_reference_share_payment_guards(village):
    client = village.test_client()
    prefix, invoices = _issued(village, client)
    path = prefix + "/sandbox/payments"
    page = client.get(path)
    assert page.status_code == 200
    resident, neighbour = invoices["sonnwil-resident"], invoices["sonnwil-neighbour"]
    for kind in ("wrong-amount", "wrong-currency", "debit", "reversal"):
        fixture = client.get(path + f"/fixtures/{resident}/{kind}/053.xml")
        assert fixture.status_code == 200 and b"SIMULATION SONNWIL" in fixture.data
        assert _statement(client, prefix, fixture).status_code == 302
        with (
            _workspace_connection(village, prefix) as connection,
            connection.cursor() as cur,
        ):
            cur.execute("SELECT MAX(id) FROM bank_statement_entries")
            entry = cur.fetchone()[0]
        page = client.get(path)
        refused = client.post(
            path + f"/confirm/{entry}",
            data={"csrf_token": _csrf(page), "invoice_id": resident, "confirm": "yes"},
        )
        assert refused.status_code == 409
    unknown = client.get(path + f"/fixtures/{resident}/unknown-reference/054.xml")
    assert unknown.status_code == 200 and b"camt.054" in unknown.data
    assert _statement(client, prefix, unknown).status_code == 302
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute("SELECT MAX(id) FROM bank_statement_entries")
        entry = cur.fetchone()[0]
    page = client.get(path)
    fields = {"csrf_token": _csrf(page), "invoice_id": resident, "confirm": "yes"}
    for _ in range(2):
        assert client.post(path + f"/confirm/{entry}", data=fields).status_code == 303
    assert (
        client.post(
            path + f"/confirm/{entry}", data={**fields, "invoice_id": neighbour}
        ).status_code
        == 409
    )
    exact = client.get(path + f"/fixtures/{neighbour}/exact/053.xml")
    for _ in range(2):
        assert _statement(client, prefix, exact).status_code == 302
    page = client.get(path)
    assert "invoice.paid" in page.text and "payment.match.confirmed" in page.text
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute(
            "SELECT invoice_id,COUNT(*) FROM invoice_lifecycle_events WHERE new_state='paid' GROUP BY invoice_id ORDER BY invoice_id"
        )
        assert cur.fetchall() == sorted([(resident, 1), (neighbour, 1)])
        cur.execute(
            "SELECT COUNT(*) FROM operator_events WHERE event_type='invoice.paid'"
        )
        assert cur.fetchone()[0] == 2
        cur.execute(
            "SELECT COUNT(*) FROM operator_events WHERE event_type='payment.match.confirmed'"
        )
        assert cur.fetchone()[0] == 1


@pytest.mark.parametrize("same_entry", [True, False])
def test_concurrent_confirmation_creates_one_paid_transition(village, same_entry):
    client = village.test_client()
    prefix, invoices = _issued(village, client)
    path = prefix + "/sandbox/payments"
    invoice = invoices["sonnwil-resident"]
    entries = []
    for message in ("053",) if same_entry else ("053", "054"):
        delivery = client.get(
            path + f"/fixtures/{invoice}/unknown-reference/{message}.xml"
        )
        assert _statement(client, prefix, delivery).status_code == 302
        with (
            _workspace_connection(village, prefix) as connection,
            connection.cursor() as cur,
        ):
            cur.execute("SELECT MAX(id) FROM bank_statement_entries")
            entries.append(cur.fetchone()[0])
    if same_entry:
        entries *= 2
    page = client.get(path)
    clients = [village.test_client(), village.test_client()]
    for clone in clients:
        for cookie in client._cookies.values():
            clone.set_cookie(
                cookie.key, cookie.value, domain=cookie.domain, path=cookie.path
            )
    barrier = Barrier(2)

    def confirm(item):
        clone, entry = item
        barrier.wait(timeout=15)
        return clone.post(
            path + f"/confirm/{entry}",
            data={"csrf_token": _csrf(page), "invoice_id": invoice, "confirm": "yes"},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(confirm, zip(clients, entries)))
    assert statuses == ([303, 303] if same_entry else [303, 409])
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute(
            "SELECT COUNT(*) FROM invoice_lifecycle_events WHERE invoice_id=%s AND new_state='paid'",
            (invoice,),
        )
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT event_type,COUNT(*) FROM operator_events WHERE event_type IN ('invoice.paid','payment.match.confirmed') GROUP BY event_type ORDER BY event_type"
        )
        assert cur.fetchall() == [("invoice.paid", 1), ("payment.match.confirmed", 1)]


def test_payment_review_rejects_issued_and_cancelled_invoices(village):
    client = village.test_client()
    prefix, invoices = _issued(village, client)
    path = prefix + "/sandbox/payments"
    resident = invoices["sonnwil-resident"]
    page = client.get(prefix + COMMUNITY + "/billing")
    assert (
        client.post(
            prefix + COMMUNITY + f"/billing/invoice/{resident}/cancel",
            data={
                "csrf_token": _csrf(page),
                "reason": "SIMULATION Stornierung vor Zahlung",
            },
        ).status_code
        == 302
    )
    for invoice in (resident, invoices["sonnwil-operator"]):
        delivery = client.get(path + f"/fixtures/{invoice}/unknown-reference/053.xml")
        assert _statement(client, prefix, delivery).status_code == 302
        with (
            _workspace_connection(village, prefix) as connection,
            connection.cursor() as cur,
        ):
            cur.execute("SELECT MAX(id) FROM bank_statement_entries")
            entry = cur.fetchone()[0]
        page = client.get(path)
        assert (
            client.post(
                path + f"/confirm/{entry}",
                data={
                    "csrf_token": _csrf(page),
                    "invoice_id": invoice,
                    "confirm": "yes",
                },
            ).status_code
            == 409
        )
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute(
            "SELECT COUNT(*) FROM invoice_lifecycle_events WHERE new_state='paid'"
        )
        assert cur.fetchone()[0] == 0


def test_payment_controls_are_private_scoped_and_csrf_protected(village, monkeypatch):
    from uuid import uuid4

    client = village.test_client()
    prefix, invoices = _issued(village, client)
    path = prefix + "/sandbox/payments"
    invoice = invoices["sonnwil-resident"]
    delivery = client.get(path + f"/fixtures/{invoice}/unknown-reference/053.xml")
    assert _statement(client, prefix, delivery).status_code == 302
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute("SELECT MAX(id) FROM bank_statement_entries")
        entry = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES ('sonnwil-foreign-leg','SIMULATION other LEG')"
        )
        cur.execute(
            "INSERT INTO invoices(community_id,participant_id,invoice_number,gross_chf,status) VALUES ('sonnwil-foreign-leg','foreign-member','SIMULATION-FOREIGN',43.20,'issued') RETURNING id"
        )
        foreign_invoice = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO bank_statement_imports(community_id,actor_id,source_name,message_type,fingerprint) VALUES ('sonnwil-foreign-leg','foreign-actor','SIMULATION-FOREIGN-SOURCE','camt.053',%s) RETURNING id",
            ("f" * 64,),
        )
        foreign_import = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO bank_statement_entries(statement_import_id,community_id,entry_reference,booking_date,amount,currency,payment_reference,credit_debit_indicator,match_decision) VALUES (%s,'sonnwil-foreign-leg','foreign-entry','2026-11-06',43.20,'CHF','SIMULATION-FOREIGN-REFERENCE','CRDT','unmatched') RETURNING id",
            (foreign_import,),
        )
        foreign_entry = cur.fetchone()[0]
        foreign_event = str(uuid4())
        cur.execute(
            "INSERT INTO operator_events(event_id,event_type,schema_version,aggregate_id,community_id,payload) VALUES (%s,'invoice.paid','operator-event/1','foreign-invoice','sonnwil-foreign-leg','{}')",
            (foreign_event,),
        )
    page = client.get(path)
    assert (
        page.status_code == 200
        and foreign_event not in page.text
        and "SIMULATION-FOREIGN-REFERENCE" not in page.text
    )
    assert (
        client.get(path + f"/fixtures/{foreign_invoice}/exact/053.xml").status_code
        == 404
    )
    assert client.get(path + f"?invoice={foreign_invoice}").status_code == 404
    assert (
        client.post(
            path + f"/confirm/{entry}", data={"invoice_id": invoice, "confirm": "yes"}
        ).status_code
        == 400
    )
    fields = {"csrf_token": _csrf(page), "invoice_id": invoice, "confirm": "yes"}
    assert (
        client.post(
            path + f"/confirm/{entry}", data={**fields, "confirm": ""}
        ).status_code
        == 400
    )
    assert (
        client.post(path + f"/confirm/{foreign_entry}", data=fields).status_code == 409
    )
    assert (
        client.post(
            path + f"/confirm/{entry}", data={**fields, "invoice_id": foreign_invoice}
        ).status_code
        == 409
    )
    other = village.test_client()
    other_prefix, _ = _start(other, "administrator")
    other_page = other.get(other_prefix + "/sandbox/payments")
    assert other_page.status_code == 200
    assert other.get(path).status_code == 410
    assert (
        other.get(
            other_prefix + f"/sandbox/payments/fixtures/{invoice}/exact/053.xml"
        ).status_code
        == 404
    )
    assert (
        other.post(
            other_prefix + f"/sandbox/payments/confirm/{entry}",
            data={
                "csrf_token": _csrf(other_page),
                "invoice_id": invoice,
                "confirm": "yes",
            },
        ).status_code
        == 409
    )
    for role in ("operator", "resident", "producer", "founder"):
        _switch(client, prefix, role)
        assert client.get(path).status_code == 403
        assert (
            client.get(path + f"/fixtures/{invoice}/exact/053.xml").status_code == 403
        )
        profile = client.get(prefix + "/dashboard")
        assert (
            client.post(
                path + f"/confirm/{entry}",
                data={**fields, "csrf_token": _csrf(profile)},
            ).status_code
            == 403
        )
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cur,
    ):
        cur.execute(
            "SELECT COUNT(*) FROM invoice_lifecycle_events WHERE new_state='paid'"
        )
        assert cur.fetchone()[0] == 0
    from flask import Flask

    import sandbox_payments

    monkeypatch.delenv("OPENLEG_SANDBOX_WORKER", raising=False)
    with pytest.raises(RuntimeError, match="requires a village worker"):
        sandbox_payments.install(Flask(__name__))
