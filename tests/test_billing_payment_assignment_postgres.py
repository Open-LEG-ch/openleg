# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dashboard payment assignment through HTTP and the PostgreSQL billing store."""

import hashlib
from html import unescape

import pytest
from psycopg2.extras import Json

import database as db
from tests import test_dashboard_access_routes, test_interest_postgres
from tests.test_dashboard_access_routes import _set_session
from tests.test_payment_acceptance_postgres import COMMUNITY, _import, _invoice

app_module = test_dashboard_access_routes.app_module
interest_database = test_interest_postgres.interest_database
pytestmark = pytest.mark.integration

ACTOR = "fictional-preparer"
AUDITOR = "fictional-auditor"
ADMIN = "fictional-admin"
BILLING = f"/leg/community/{COMMUNITY}/billing"


@pytest.fixture
def workspace(interest_database, app_module):
    for building in (ACTOR, AUDITOR, ADMIN):
        assert db.save_building(
            building,
            building + "@example.invalid",
            {"address": "Fictional address", "lat": 47.2, "lon": 8.2},
            {},
            verified=True,
        )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities (community_id, name) VALUES (%s, 'Fictional LEG')",
            (COMMUNITY,),
        )
        for building, role in (
            (ACTOR, "billing_preparer"),
            (AUDITOR, "auditor"),
            (ADMIN, "admin"),
        ):
            cur.execute(
                """INSERT INTO community_members
                   (community_id, building_id, role, status, access_roles)
                   VALUES (%s, %s, %s, 'confirmed', %s)""",
                (
                    COMMUNITY,
                    building,
                    "admin" if building == ADMIN else "member",
                    Json([role]),
                ),
            )
    for capability in ("billing.read", "payments.read"):
        db.create_operator_api_client(
            COMMUNITY,
            ACTOR,
            capability,
            [capability],
            hashlib.sha256(capability.encode()).hexdigest(),
            "https://receiver.example.org/events",
        )
    client = app_module.web.test_client()
    _set_session(client, ACTOR)
    return client


def _assign(client, entry, invoice, **overrides):
    return client.post(
        f"{BILLING}/statements/{entry}/assign",
        data={
            "csrf_token": "csrf-secret",
            "invoice_id": invoice,
            "confirm": "yes",
            **overrides,
        },
    )


def test_preparer_assigns_exact_credit_and_can_replay(workspace):
    invoice = _invoice()
    entry, _ = _import()
    for _ in range(2):
        response = _assign(workspace, entry, invoice)
        assert response.status_code == 303
    page = workspace.get(BILLING)
    assert "Zahlung zugeordnet" in page.text
    assert f"/statements/{entry}/assign" not in page.text

    (statement,) = db.list_bank_statement_entries(COMMUNITY)
    assert (statement["match_decision"], statement["invoice_id"]) == (
        "matched",
        invoice,
    )
    (paid,) = [
        event
        for event in db.list_community_invoice_events(COMMUNITY)
        if event["event_type"] == "paid"
    ]
    actor = {"actor_kind": "human", "initiating_client_id": None, "agent_label": None}
    assert paid["actor_id"] == ACTOR
    assert paid["actor"] == actor
    assert paid["payment_match"] == {"entry_id": entry, "decision": "matched"}
    assert (paid["previous_state"], paid["new_state"], paid["reference"]) == (
        "delivered",
        "paid",
        "unknown reference",
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT e.event_type, e.aggregate_id, e.payload, e.actor, c.name
               FROM operator_events e JOIN operator_webhook_deliveries d USING (event_id)
               JOIN operator_api_clients c ON c.id=d.client_id
               ORDER BY e.event_type""",
        )
        assert [dict(row) for row in cur.fetchall()] == [
            {
                "event_type": "invoice.paid",
                "aggregate_id": str(invoice),
                "payload": {"status": "paid"},
                "actor": actor,
                "name": "billing.read",
            },
            {
                "event_type": "payment.match.confirmed",
                "aggregate_id": str(entry),
                "payload": {"status": "matched"},
                "actor": actor,
                "name": "payments.read",
            },
        ]


@pytest.mark.parametrize(
    ("case", "status", "message"),
    [
        ("debit", 409, "Das ist eine Belastung, keine Gutschrift."),
        ("reversal", 409, "Die Bank hat die Zahlung zurückgebucht."),
        ("underpayment", 409, "Der Betrag ist zu tief."),
        ("overpayment", 409, "Der Betrag ist zu hoch."),
        ("currency", 409, "Die Währung ist nicht CHF."),
        ("missing_reference", 409, "Eine Zahlungsreferenz ist erforderlich."),
        ("issued", 409, "Stellen Sie die Rechnung zuerst zu"),
        ("foreign_invoice", 409, "Rechnung nicht gefunden."),
        (
            "foreign_entry",
            409,
            "Dieser Eintrag ist nicht mehr zur Zuordnung verfügbar.",
        ),
        ("missing_confirmation", 400, "Bestätigen Sie die Prüfung"),
        ("csrf", 400, None),
        ("auditor", 403, None),
        ("anonymous", 401, None),
    ],
)
def test_refused_assignment_has_no_financial_side_effects(
    workspace, case, status, message
):
    from tests.test_store_billing_postgres import _insert_issued_invoice

    invoice = _insert_issued_invoice("ISSUED-ONLY") if case == "issued" else _invoice()
    entry, _ = _import(
        **{
            "debit": {"direction": "DBIT"},
            "reversal": {"is_reversal": True},
            "underpayment": {"amount": "9.66"},
            "overpayment": {"amount": "9.68"},
            "currency": {"currency": "EUR"},
            "missing_reference": {"reference": ""},
        }.get(case, {})
    )
    entry_scope = COMMUNITY
    invoice_scope = COMMUNITY
    if case in {"foreign_invoice", "foreign_entry", "reversal"}:
        with db.get_connection() as conn, conn.cursor() as cur:
            if case == "foreign_invoice":
                # Move both records together so a missing SQL scope guard
                # could accept the otherwise eligible foreign invoice.
                cur.execute("ALTER TABLE invoice_lifecycle_events DISABLE TRIGGER USER")
                cur.execute(
                    "UPDATE invoice_lifecycle_events SET community_id='foreign' WHERE invoice_id=%s",
                    (invoice,),
                )
                cur.execute("ALTER TABLE invoice_lifecycle_events ENABLE TRIGGER USER")
                cur.execute("ALTER TABLE invoices DISABLE TRIGGER USER")
                cur.execute(
                    "UPDATE invoices SET community_id='foreign' WHERE id=%s", (invoice,)
                )
                cur.execute("ALTER TABLE invoices ENABLE TRIGGER USER")
                invoice_scope = "foreign"
            elif case == "foreign_entry":
                cur.execute("UPDATE bank_statement_imports SET community_id='foreign'")
                cur.execute("UPDATE bank_statement_entries SET community_id='foreign'")
                entry_scope = "foreign"
            else:
                # A reversal from an older import can still have a reviewable decision.
                cur.execute(
                    "UPDATE bank_statement_entries SET match_decision='mismatch'"
                )
    if case == "auditor":
        _set_session(workspace, AUDITOR)
        page = workspace.get(BILLING)
        assert page.status_code == 200
        assert f"/statements/{entry}/assign" not in page.text
    if case == "anonymous":
        with workspace.session_transaction() as state:
            state.pop("dashboard_building_id")
    before = db.list_bank_statement_entries(entry_scope)
    audit = db.list_community_invoice_events(invoice_scope)

    response = _assign(
        workspace,
        entry,
        invoice,
        **{"missing_confirmation": {"confirm": ""}, "csrf": {"csrf_token": ""}}.get(
            case, {}
        ),
    )

    assert db.list_bank_statement_entries(entry_scope) == before
    assert db.list_community_invoice_events(invoice_scope) == audit
    assert (
        db.list_community_operator_events(
            COMMUNITY, event_types=("invoice.paid", "payment.match.confirmed")
        )
        == []
    )
    assert response.status_code == status
    if message:
        assert message in response.text
    if case == "missing_reference":
        assert "Die Zahlung hat keine Referenz." in response.text
        assert f'action="{BILLING}/statements/{entry}/assign' not in response.text
        assert f'href="{BILLING}?lang=fr-CH#statement-assignments"' in response.text


def test_stale_selection_cannot_change_the_booked_invoice(workspace):
    invoice = _invoice()
    other = _invoice("LEG-PAYMENT-2")
    entry, _ = _import()
    assert _assign(workspace, entry, invoice).status_code == 303

    response = _assign(workspace, entry, other)

    assert response.status_code == 409
    assert "Dieser Eintrag ist nicht mehr zur Zuordnung verfügbar." in response.text
    (statement,) = db.list_bank_statement_entries(COMMUNITY)
    assert statement["invoice_id"] == invoice
    assert not any(
        event["event_type"] == "paid"
        for event in db.list_invoice_events(other, COMMUNITY)
    )


def test_workspace_renders_imported_bank_details_and_only_delivered_leg_invoices(
    workspace,
):
    from io import BytesIO

    from tests.test_payment_reconciliation import CAMT_053
    from tests.test_store_billing_postgres import _insert_issued_invoice

    invoice = _invoice("DELIVERED-LOCAL")
    _insert_issued_invoice("ISSUED-ONLY")
    foreign = _invoice("DELIVERED-FOREIGN")
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("ALTER TABLE invoices DISABLE TRIGGER USER")
        cur.execute(
            "UPDATE invoices SET community_id='foreign' WHERE id=%s", (foreign,)
        )
        cur.execute("ALTER TABLE invoices ENABLE TRIGGER USER")
    statement = CAMT_053.replace(b"120.50", b"1071.70").replace(
        b"<RmtInf><Ustrd>LEG-2026-000042</Ustrd></RmtInf>",
        b"<RltdPties><Dbtr><Pty><Nm>Fictional &amp; Co.</Nm></Pty></Dbtr>"
        b"<Cdtr><Pty><Nm>Not the debtor</Nm></Pty></Cdtr></RltdPties>"
        b"<RmtInf><Ustrd>October electricity</Ustrd>"
        b"<Strd><CdtrRefInf><Ref>Fictional bank reference</Ref></CdtrRefInf></Strd></RmtInf>",
    )
    _set_session(workspace, ADMIN)
    imported = workspace.post(
        f"{BILLING}/statements",
        data={
            "csrf_token": "csrf-secret",
            "statement": (BytesIO(statement), "fictional.xml"),
        },
    )
    assert imported.status_code == 302
    partial = CAMT_053.replace(b"120.50", b"4.00").replace(
        b"LEG-2026-000042", b"DELIVERED-LOCAL"
    )
    assert (
        workspace.post(
            f"{BILLING}/statements",
            data={
                "csrf_token": "csrf-secret",
                "statement": (BytesIO(partial), "partial.xml"),
            },
        ).status_code
        == 302
    )
    _set_session(workspace, ACTOR)
    page = workspace.get(BILLING)

    assert page.status_code == 200
    form = page.text.split("/assign#", 1)[1].split("</form>", 1)[0]
    assert f'<option value="{invoice}">DELIVERED-LOCAL' in form
    assert "ISSUED-ONLY" not in form
    assert "DELIVERED-FOREIGN" not in form
    assert (
        "Ich habe Betrag, Währung, Richtung, Referenz und Rechnungsstatus geprüft."
        in form
    )
    assert "Zahlung zuordnen" in form
    assert page.text.count("/assign#") == 1
    assert "Der Betrag ist zu tief." in page.text
    assert "Referenz gehört zu" in page.text
    assert "Es fehlen" in page.text and "CHF 5.67" in page.text
    for value in (
        "CHF 1'071.70",
        "14. September 2026",
        "Fictional bank reference",
        "Fictional & Co.",
        "October electricity",
    ):
        assert value in unescape(page.text)
    assert "Not the debtor" not in page.text
