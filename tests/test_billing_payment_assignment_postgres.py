# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dashboard payment assignment through HTTP and the PostgreSQL billing store."""

import hashlib
from html import unescape
from urllib.parse import parse_qs, urlsplit

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
PAYMENTS = f"{BILLING}?section=payments"


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


def _follow_assignment(client, response):
    assert response.status_code == 303
    location = urlsplit(response.location)
    query = parse_qs(location.query)
    assert location.path == BILLING
    assert location.fragment == "payments"
    assert query["section"] == ["payments"]
    assert len(query["assignment"]) == 1
    page = client.get(response.location)
    assert page.status_code == 200
    assert f'href="{PAYMENTS}#payments" aria-current="page"' in " ".join(
        page.text.split()
    )
    return page


def _assignment_outcome(client, response):
    page = _follow_assignment(client, response)
    if 'id="assignment-feedback" role="status"' in page.text:
        return "assigned"
    assert 'id="assignment-feedback" role="alert"' in page.text
    return "refused"


def test_preparer_assigns_exact_credit_and_can_replay(workspace):
    invoice = _invoice()
    entry, _ = _import()
    for _ in range(2):
        response = _assign(workspace, entry, invoice)
        assert _assignment_outcome(workspace, response) == "assigned"
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
    ("case", "message"),
    [
        ("debit", "Das ist eine Belastung, keine Gutschrift."),
        ("reversal", "Die Bank hat die Zahlung zurückgebucht."),
        ("underpayment", "Der Betrag ist zu tief."),
        ("overpayment", "Der Betrag ist zu hoch."),
        ("currency", "Die Währung ist nicht CHF."),
        ("missing_reference", "Die Zahlung hat keine Referenz."),
        ("issued", "Stellen Sie die Rechnung zuerst zu"),
        ("foreign_invoice", "Rechnung nicht gefunden."),
        (
            "foreign_entry",
            "Dieser Eintrag ist nicht mehr zur Zuordnung verfügbar.",
        ),
        ("missing_confirmation", "Bestätigen Sie die Prüfung"),
        ("csrf", "Das Formular ist abgelaufen."),
        ("auditor", "Sie dürfen in dieser LEG keine Zahlungen zuordnen."),
        ("anonymous", None),
    ],
)
def test_refused_assignment_has_no_financial_side_effects(workspace, case, message):
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
    if case == "anonymous":
        assert response.status_code == 401
        return
    response = _follow_assignment(workspace, response)
    feedback = response.text.partition('id="assignment-feedback" role="alert"')[2]
    feedback = feedback.partition("</p>")[0]
    assert message in feedback
    if case == "missing_reference":
        assert (
            "Fordern Sie bei der Bank einen Kontoauszug mit Zahlungsreferenz an."
            in feedback
        )
        assert f'action="{BILLING}/statements/{entry}/assign' not in response.text


def test_stale_selection_cannot_change_the_booked_invoice(workspace):
    invoice = _invoice()
    other = _invoice("LEG-PAYMENT-2")
    entry, _ = _import()
    assert _assign(workspace, entry, invoice).status_code == 303

    response = _assign(workspace, entry, other)

    response = _follow_assignment(workspace, response)
    assert "Dieser Eintrag ist nicht mehr zur Zuordnung verfügbar." in response.text
    (statement,) = db.list_bank_statement_entries(COMMUNITY)
    assert statement["invoice_id"] == invoice
    assert not any(
        event["event_type"] == "paid"
        for event in db.list_invoice_events(other, COMMUNITY)
    )


def test_assignment_notice_exposes_no_billing_data_without_audit_access(workspace):
    invoice = _invoice("INVOICE-PRIVATE-TO-BILLING")
    entry, _ = _import()
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE community_members SET access_roles='[]' WHERE community_id=%s AND building_id=%s",
            (COMMUNITY, ACTOR),
        )
    response = _assign(workspace, entry, invoice)
    assert response.status_code == 303
    page = workspace.get(response.location)
    assert 'id="payments"' in page.text
    assert "Sie dürfen in dieser LEG keine Zahlungen zuordnen." in page.text
    assert "INVOICE-PRIVATE-TO-BILLING" not in page.text
    assert workspace.get(response.location).status_code == 403
    assert not db.list_community_operator_events(
        COMMUNITY, event_types=("invoice.paid",)
    )


def test_payment_assignment_feedback_follows_its_own_redirect(workspace):
    invoice = _invoice()
    entry, _ = _import()
    assigned = _assign(workspace, entry, invoice)
    refused = _assign(workspace, entry, invoice, confirm="no")

    assert 'id="assignment-feedback"' not in workspace.get(PAYMENTS).text
    _set_session(workspace, AUDITOR)
    assert 'id="assignment-feedback"' not in workspace.get(assigned.location).text
    _set_session(workspace, ACTOR)
    assert assigned.location != refused.location
    first = _follow_assignment(workspace, assigned)
    second = _follow_assignment(workspace, refused)
    assert 'id="assignment-feedback" role="status"' in first.text
    assert 'id="assignment-feedback" role="alert"' in second.text
    assert 'id="assignment-feedback"' not in workspace.get(assigned.location).text
    assert 'id="assignment-feedback"' not in workspace.get(refused.location).text


def test_payment_assignment_feedback_survives_a_failed_get(workspace, monkeypatch):
    import dashboard

    invoice = _invoice()
    entry, _ = _import()
    response = _assign(workspace, entry, invoice, confirm="no")

    def unavailable(*args, **kwargs):
        raise db.BillingStoreError("fictional outage")

    with monkeypatch.context() as failure:
        failure.setattr(dashboard, "leg_billing_workspace_view", unavailable)
        assert workspace.get(response.location).status_code == 503
    page = workspace.get(response.location)
    assert 'id="assignment-feedback" role="alert"' in page.text
    assert 'id="assignment-feedback"' not in workspace.get(response.location).text


def test_assignment_refuses_unreadable_invoice_total(workspace):
    unreadable = _invoice("UNREADABLE-INVOICE")
    readable = _invoice("READABLE-INVOICE")
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("ALTER TABLE invoices DISABLE TRIGGER USER")
        cur.execute("UPDATE invoices SET gross_chf=NULL WHERE id=%s", (unreadable,))
        cur.execute("ALTER TABLE invoices ENABLE TRIGGER USER")

    _upload_xml(workspace, _statement_xml(reference="unknown"))
    (entry,) = db.list_bank_statement_entries(COMMUNITY)
    refusal = _follow_assignment(workspace, _assign(workspace, entry["id"], unreadable))
    assert 'id="assignment-feedback" role="alert"' in refusal.text
    assert "Die Rechnung hat keinen gültigen Gesamtbetrag." in refusal.text
    assert (
        "Prüfen Sie die Rechnung oder wählen Sie eine andere zugestellte Rechnung."
        in refusal.text
    )
    assert f'<option value="{unreadable}">' not in refusal.text
    assert f'<option value="{readable}">' in refusal.text
    assert _entry_booking(entry["id"]) == ("unmatched", None)

    _upload_xml(
        workspace,
        _statement_xml(statement="statement-2", reference="UNREADABLE-INVOICE"),
    )
    imported = db.list_bank_statement_entries(COMMUNITY)[0]
    assert imported["match_decision"] == "mismatch"
    assert not _paid_events()


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
    form = page.text.split("/assign", 1)[1].split("</form>", 1)[0]
    assert f'<option value="{invoice}">DELIVERED-LOCAL' in form
    assert "ISSUED-ONLY" not in form
    assert "DELIVERED-FOREIGN" not in form
    assert (
        "Ich habe Betrag, Währung, Richtung, Referenz und Rechnungsstatus geprüft."
        in form
    )
    assert "Zahlung zuordnen" in form
    assert page.text.count("/assign") == 1
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


def _upload_xml(workspace, *bodies):
    from io import BytesIO

    _set_session(workspace, ADMIN)
    for body in bodies:
        assert (
            workspace.post(
                f"{BILLING}/statements",
                data={
                    "csrf_token": "csrf-secret",
                    "statement": (BytesIO(body), "fictional.xml"),
                },
            ).status_code
            == 302
        )
    _set_session(workspace, ACTOR)


def _statement_xml(
    *,
    statement="statement-1",
    reference="LEG-2026-000042",
    servicer="bank-entry-1",
    account="fictional-account",
    account_is_iban=False,
    details=b"",
    reversal=b"",
):
    from tests.test_payment_reconciliation import CAMT_053

    account_identifier = (
        f"<IBAN>{account}</IBAN>"
        if account_is_iban
        else f"<Othr><Id>{account}</Id></Othr>"
    )
    account_xml = f"<Acct><Id>{account_identifier}</Id></Acct>" if account else ""
    return (
        CAMT_053.replace(b"statement-1</Id>", f"{statement}</Id>{account_xml}".encode())
        .replace(b"120.50", b"9.67")
        .replace(b"LEG-2026-000042", reference.encode())
        .replace(
            b"<AcctSvcrRef>bank-entry-1</AcctSvcrRef>",
            f"<AcctSvcrRef>{servicer}</AcctSvcrRef>".encode() if servicer else b"",
        )
        .replace(b"<Sts>", reversal + b"<Sts>")
        .replace(b"<RmtInf>", details + b"<RmtInf>")
    )


@pytest.mark.parametrize("automatic", [False, True])
def test_camt_numeric_reversal_cannot_book_a_payment(workspace, automatic):
    invoice = _invoice("DELIVERED-INVOICE")
    _upload_xml(
        workspace,
        _statement_xml(
            reference="DELIVERED-INVOICE" if automatic else "unknown",
            reversal=b"<RvslInd>1</RvslInd>",
        ),
    )
    (entry,) = db.list_bank_statement_entries(COMMUNITY)
    if not automatic:
        assert (
            _assignment_outcome(workspace, _assign(workspace, entry["id"], invoice))
            == "refused"
        )
    assert entry["match_decision"] == "reversal"
    assert not any(
        event["event_type"] == "paid"
        for event in db.list_invoice_events(invoice, COMMUNITY)
    )


def test_newer_history_cannot_hide_an_unresolved_entry(workspace):
    _invoice("DELIVERED-INVOICE")
    _upload_xml(workspace, _statement_xml(reference="old unresolved reference"))
    (older,) = db.list_bank_statement_entries(COMMUNITY)
    statement = _statement_xml(statement="history", reversal=b"<RvslInd>true</RvslInd>")
    start = statement.index(b"<Ntry>")
    end = statement.index(b"</Ntry>") + len(b"</Ntry>")
    history = b"".join(
        statement[start:end].replace(b"bank-entry-1", f"reversal-{index}".encode())
        for index in range(200)
    )
    _upload_xml(workspace, statement[:start] + history + statement[end:])
    page = workspace.get(BILLING)
    assert page.status_code == 200
    assert "old unresolved reference" in page.text
    assert f"/statements/{older['id']}/assign" in page.text


def test_debtor_contact_name_is_not_a_party_name(workspace):
    _invoice()
    _upload_xml(
        workspace,
        _statement_xml(
            details=b"<RltdPties><Dbtr><Pty><CtctDtls><Nm>Fictional contact only</Nm>"
            b"</CtctDtls></Pty></Dbtr></RltdPties>",
        ),
    )
    (entry,) = db.list_bank_statement_entries(COMMUNITY)
    assert entry["debtor_name"] is None
    assert "Fictional contact only" not in workspace.get(BILLING).text


def _entry_booking(entry_id):
    entry = next(
        row
        for row in db.list_bank_statement_entries(COMMUNITY)
        if row["id"] == entry_id
    )
    return entry["match_decision"], entry["invoice_id"]


def test_older_unresolved_entries_have_a_link_when_review_queue_is_full(workspace):
    _invoice()
    _upload_xml(workspace, _statement_xml(reference="old unresolved reference"))
    (older,) = db.list_bank_statement_entries(COMMUNITY)
    statement = _statement_xml(
        statement="newer", reference="newer unresolved reference"
    )
    start = statement.index(b"<Ntry>")
    end = statement.index(b"</Ntry>") + len(b"</Ntry>")
    newer = b"".join(
        statement[start:end].replace(b"bank-entry-1", f"newer-{index}".encode())
        for index in range(200)
    )
    _upload_xml(workspace, statement[:start] + newer + statement[end:])
    first_page = workspace.get(BILLING)
    assert "old unresolved reference" not in first_page.text
    assert "Ältere offene Buchungen" in first_page.text
    older_path = f"{PAYMENTS}&statement_before={older['id'] + 1}#payments"
    assert f'href="{older_path}"' in unescape(first_page.text)
    next_page = workspace.get(older_path)
    assert "old unresolved reference" in next_page.text
    assert "Neueste offene Buchungen" in next_page.text


def _paid_events():
    return db.list_community_operator_events(COMMUNITY, event_types=("invoice.paid",))


def _review_token(page):
    import re

    assert (
        "Ich habe alle aufgeführten Zahlungen geprüft. Es ist eine andere Zahlung."
        in page.text
    )
    return re.search(r'name="duplicate_review" value="([^"]+)"', page.text).group(1)


def _override(client, entry, invoice, token):
    return _assign(
        client, entry, invoice, duplicate_review=token, confirm_distinct="yes"
    )


def _booking_on(xml, booking_date):
    return xml.replace(b"2026-09-14", booking_date.encode())


@pytest.mark.parametrize("reverse", [False, True])
def test_all_rows_are_visible_before_auto_booking(workspace, reverse):
    first = _invoice("FIRST-INVOICE")
    _invoice("OTHER-INVOICE")

    def row(reference, own_bank_ref, shared_bank_ref, day):
        xml = _booking_on(
            _with_parent_reference(
                _statement_xml(reference=reference, servicer=own_bank_ref),
                shared_bank_ref,
            ),
            day,
        )
        return xml[xml.index(b"<Ntry>") : xml.index(b"</Ntry>") + len(b"</Ntry>")]

    # The bridge links to both payments. Its own reference names no invoice.
    xml = _statement_xml()
    start, end = xml.index(b"<Ntry>"), xml.index(b"</Ntry>") + len(b"</Ntry>")
    rows = [
        row("FIRST-INVOICE", "entry-one", "shared-left", "2026-09-14"),
        row("no invoice", "shared-right", "shared-left", "2026-09-24"),
        row("OTHER-INVOICE", "entry-three", "shared-right", "2026-10-04"),
    ]
    if reverse:
        rows.reverse()
    _upload_xml(workspace, xml[:start] + b"".join(rows) + xml[end:])
    entries = db.list_bank_statement_entries(COMMUNITY)
    assert [e["duplicate_reason"] for e in entries] == [
        "possible_duplicate_payment"
    ] * 3
    assert not _paid_events()
    candidate = next(e for e in entries if e["payment_reference"] == "FIRST-INVOICE")
    page = _follow_assignment(workspace, _assign(workspace, candidate["id"], first))
    assert (
        _assignment_outcome(
            workspace, _override(workspace, candidate["id"], first, _review_token(page))
        )
        == "assigned"
    )
    assert len(_paid_events()) == 1


def test_override_shows_and_rechecks_the_complete_set(workspace, app_module):
    _invoice("FIRST-INVOICE")
    second = _invoice("SECOND-INVOICE")
    candidate_invoice = _invoice("CANDIDATE")
    _upload_xml(
        workspace,
        _statement_xml(
            statement="first-paid", reference="FIRST-INVOICE", servicer="bank-a"
        ),
        _statement_xml(
            statement="second-held", reference="SECOND-INVOICE", servicer="bank-b"
        ),
    )
    second_row, first_row = db.list_bank_statement_entries(COMMUNITY)
    page = _follow_assignment(workspace, _assign(workspace, second_row["id"], second))
    assert (
        _assignment_outcome(
            workspace,
            _override(workspace, second_row["id"], second, _review_token(page)),
        )
        == "assigned"
    )
    _upload_xml(
        workspace,
        _statement_xml(statement="candidate", reference="CANDIDATE", servicer="bank-c"),
    )
    candidate = db.list_bank_statement_entries(COMMUNITY)[0]
    entry = candidate["id"]
    page = _follow_assignment(workspace, _assign(workspace, entry, candidate_invoice))
    review = page.text.split('id="duplicate-review-matches"', 1)[1].split("</ul>", 1)[0]
    for value in (
        "first-paid",
        "second-held",
        "FIRST-INVOICE",
        "SECOND-INVOICE",
        "9.67",
        "14. September",
    ):
        assert value in review
    old = _review_token(page)
    for fields in ({"confirm_distinct": ""}, {"csrf_token": "invalid"}):
        assert (
            _assignment_outcome(
                workspace,
                _assign(
                    workspace, entry, candidate_invoice, duplicate_review=old, **fields
                ),
            )
            == "refused"
        )
    assert len(_paid_events()) == 2

    _upload_xml(
        workspace,
        _statement_xml(
            statement="late-match", reference="late-reference", servicer="bank-d"
        ),
    )
    late = db.list_bank_statement_entries(COMMUNITY)[0]
    refreshed = _follow_assignment(
        workspace, _override(workspace, entry, candidate_invoice, old)
    )
    assert (
        "late-match"
        in refreshed.text.split('id="duplicate-review-matches"', 1)[1].split(
            "</ul>", 1
        )[0]
    )
    assert len(_paid_events()) == 2

    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    token = _review_token(refreshed)
    barrier = Barrier(2)

    def confirm(_):
        with app_module.web.test_client() as client:
            _set_session(client, ACTOR)
            barrier.wait(timeout=10)
            return _assignment_outcome(
                client, _override(client, entry, candidate_invoice, token)
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(confirm, range(2))) == ["assigned", "assigned"]
    assert len(_paid_events()) == 3
    assert _entry_booking(entry) == ("matched", candidate_invoice)
    [audit] = [
        event
        for event in db.list_community_invoice_events(COMMUNITY)
        if event["invoice_id"] == candidate_invoice and event["event_type"] == "paid"
    ]
    assert audit["payment_match"]["duplicate_override"]["reviewed_entry_ids"] == [
        first_row["id"],
        second_row["id"],
        late["id"],
    ]


def _with_servicer(xml, bic):
    return xml.replace(
        b"</Acct>",
        f"<Svcr><FinInstnId><BICFI>{bic}</BICFI></FinInstnId></Svcr></Acct>".encode(),
    )


@pytest.mark.parametrize("case", ["distinct-banks", "iban-and-other", "identical"])
def test_account_evidence_only_hard_refuses_an_identical_booking(workspace, case):
    first, second = _invoice("FIRST-INVOICE"), _invoice("SECOND-INVOICE")
    iban = {"account": "CH0000000000000000000", "account_is_iban": True}
    if case == "distinct-banks":
        original = _with_servicer(_statement_xml(reference="FIRST-INVOICE"), "TSTACHZZ")
        repeat = _with_servicer(
            _statement_xml(statement="repeat", reference="SECOND-INVOICE"), "TSTBCHZZ"
        )
    else:
        original = _statement_xml(reference="FIRST-INVOICE", **iban)
        repeat = _statement_xml(
            statement="repeat",
            reference="SECOND-INVOICE",
            **(iban if case == "identical" else {}),
        )
    _upload_xml(workspace, original, repeat)
    candidate, prior = db.list_bank_statement_entries(COMMUNITY)
    assert _entry_booking(prior["id"]) == ("matched", first)
    assert candidate["duplicate_reason"] == "possible_duplicate_payment"
    page = _follow_assignment(workspace, _assign(workspace, candidate["id"], second))
    if case == "identical":
        assert (
            "Diese Zahlung ist bereits auf Rechnung FIRST-INVOICE gebucht." in page.text
        )
        assert 'name="duplicate_review"' not in page.text
        result = _override(workspace, candidate["id"], second, "invalid-review")
        assert _assignment_outcome(workspace, result) == "refused"
        assert len(_paid_events()) == 1
    else:
        token = _review_token(page)
        assert len(_paid_events()) == 1
        assert (
            _assignment_outcome(
                workspace, _override(workspace, candidate["id"], second, token)
            )
            == "assigned"
        )
        assert len(_paid_events()) == 2


@pytest.mark.parametrize(
    ("source_reference", "other_reference", "held"),
    [
        ("000000000000000000000000001", "000000000000000000000000002", False),
        ("Lease AB C", "Lease A BC", False),
        ("LEG-2026-000042", "leg 2026 000042", True),
        ("000000000000000000000000001", "00000 00000 00000 00000 0000001", True),
        ("RF18539007547034", "rf18 5390 0754 7034", True),
    ],
)
def test_same_amount_outside_date_window_requires_reference_evidence(
    workspace, source_reference, other_reference, held
):
    first = _invoice(
        source_reference if source_reference.startswith("LEG-") else "FIRST-INVOICE"
    )
    second = _invoice("SECOND-INVOICE")
    _upload_xml(
        workspace, _statement_xml(reference=source_reference, servicer="booking-a")
    )
    [original] = db.list_bank_statement_entries(COMMUNITY)
    if original["match_decision"] != "matched":
        assert (
            _assignment_outcome(workspace, _assign(workspace, original["id"], first))
            == "assigned"
        )
    assert _entry_booking(original["id"]) == ("matched", first)
    _upload_xml(
        workspace,
        _booking_on(
            _statement_xml(
                statement="second-export",
                reference=other_reference,
                servicer="booking-b",
            ),
            "2026-10-14" if not held else "2026-09-24",
        ),
    )
    candidate = db.list_bank_statement_entries(COMMUNITY)[0]
    assert candidate["payment_reference"] == other_reference
    response = _assign(workspace, candidate["id"], second)
    if held:
        assert candidate["duplicate_reason"] == "possible_duplicate_payment"
        _review_token(_follow_assignment(workspace, response))
        assert len(_paid_events()) == 1
    else:
        assert candidate["duplicate_reason"] is None
        assert _assignment_outcome(workspace, response) == "assigned"
        assert len(_paid_events()) == 2


def _with_parent_reference(xml, reference):
    return xml.replace(
        b"</BookgDt>", f"</BookgDt><AcctSvcrRef> {reference} </AcctSvcrRef>".encode()
    )


def test_single_transaction_batch_keeps_its_entry_reference(workspace):
    _invoice("FIRST-INVOICE")
    second = _invoice("SECOND-INVOICE")
    original = _with_parent_reference(
        _statement_xml(reference="FIRST-INVOICE", servicer="child-booking"),
        "parent-booking",
    ).replace(
        b"<NtryDtls><TxDtls>",
        b'<NtryDtls><Btch><NbOfTxs>1</NbOfTxs></Btch><TxDtls><Amt Ccy="CHF">9.67</Amt>',
    )
    repeat = _with_parent_reference(
        _statement_xml(statement="repeat", reference="SECOND-INVOICE", servicer=""),
        "parent-booking",
    )
    _upload_xml(workspace, original, _booking_on(repeat, "2026-09-24"))
    candidate, earlier = db.list_bank_statement_entries(COMMUNITY)
    assert earlier["match_decision"] == "matched"
    assert candidate["duplicate_reason"] == "possible_duplicate_payment"
    _review_token(
        _follow_assignment(workspace, _assign(workspace, candidate["id"], second))
    )
    assert len(_paid_events()) == 1


def test_booking_one_reviewed_candidate_invalidates_the_others_review(
    workspace, app_module
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    invoices = [_invoice("FIRST-INVOICE"), _invoice("SECOND-INVOICE")]
    _upload_xml(
        workspace,
        _statement_xml(reference="open", servicer="bank-a"),
        _statement_xml(statement="bank-b", reference="open", servicer="bank-b"),
    )
    bookings = []
    for row, invoice in zip(
        db.list_bank_statement_entries(COMMUNITY), invoices, strict=True
    ):
        page = _follow_assignment(workspace, _assign(workspace, row["id"], invoice))
        bookings.append((row["id"], invoice, _review_token(page)))

    barrier = Barrier(2)

    def confirm(booking):
        with app_module.web.test_client() as client:
            _set_session(client, ACTOR)
            barrier.wait(timeout=10)
            return _assignment_outcome(client, _override(client, *booking))

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(confirm, bookings)) == ["assigned", "refused"]
    assert len(_paid_events()) == 1


def test_equal_local_references_in_one_file_keep_both_statement_origins(workspace):
    first = _invoice("FIRST-INVOICE")
    second = _invoice("SECOND-INVOICE")
    first_statement = _with_servicer(
        _statement_xml(reference="FIRST-INVOICE"), "TSTACHZZ"
    )
    second_statement = _with_servicer(
        _statement_xml(statement="another-statement", reference="SECOND-INVOICE"),
        "TSTBCHZZ",
    )
    start = second_statement.index(b"<Stmt>")
    end = second_statement.index(b"</Stmt>") + len(b"</Stmt>")
    xml = first_statement.replace(
        b"</BkToCstmrStmt>", second_statement[start:end] + b"</BkToCstmrStmt>"
    )
    _upload_xml(workspace, xml)
    newer, earlier = db.list_bank_statement_entries(COMMUNITY)
    assert not _paid_events()
    review = _follow_assignment(workspace, _assign(workspace, earlier["id"], first))
    assert (
        "another-statement"
        in review.text.split('id="duplicate-review-matches"', 1)[1].split("</ul>", 1)[0]
    )
    assert (
        _assignment_outcome(
            workspace, _override(workspace, earlier["id"], first, _review_token(review))
        )
        == "assigned"
    )
    review = _follow_assignment(workspace, _assign(workspace, newer["id"], second))
    assert (
        _assignment_outcome(
            workspace, _override(workspace, newer["id"], second, _review_token(review))
        )
        == "assigned"
    )
    assert len(_paid_events()) == 2


@pytest.mark.parametrize(
    ("same_kind", "later"), [(False, False), (False, True), (True, False)]
)
def test_only_same_kind_bank_references_can_hard_refuse(workspace, same_kind, later):
    first = _invoice("FIRST-INVOICE")
    second = _invoice("SECOND-INVOICE")
    account = {"account": "CH0000000000000000000", "account_is_iban": True}
    original = _statement_xml(reference="FIRST-INVOICE", **account).replace(
        b"AcctSvcrRef", b"NtryRef"
    )
    repeat = _statement_xml(statement="repeat", reference="SECOND-INVOICE", **account)
    if same_kind:
        repeat = repeat.replace(b"AcctSvcrRef", b"NtryRef")
    if later:
        repeat = _booking_on(repeat, "2026-09-24")
    _upload_xml(workspace, original, repeat)
    candidate, prior = db.list_bank_statement_entries(COMMUNITY)
    assert _entry_booking(prior["id"]) == ("matched", first)
    assert len(_paid_events()) == 1
    entry = candidate["id"]
    page = _follow_assignment(workspace, _assign(workspace, entry, second))
    if same_kind:
        assert (
            "Diese Zahlung ist bereits auf Rechnung FIRST-INVOICE gebucht." in page.text
        )
        assert 'name="duplicate_review"' not in page.text
        assert (
            _assignment_outcome(
                workspace, _override(workspace, entry, second, "invalid-review")
            )
            == "refused"
        )
        assert len(_paid_events()) == 1
    else:
        assert "gleiche Bankreferenz" in page.text
        assert (
            _assignment_outcome(
                workspace, _override(workspace, entry, second, _review_token(page))
            )
            == "assigned"
        )
        assert _entry_booking(entry) == ("matched", second)
        assert len(_paid_events()) == 2


def test_whitespace_in_bank_reference_does_not_prove_identity(workspace):
    _invoice("FIRST-INVOICE")
    second = _invoice("SECOND-INVOICE")
    account = {"account": "CH0000000000000000000", "account_is_iban": True}
    _upload_xml(
        workspace,
        _statement_xml(reference="FIRST-INVOICE", servicer=" PAYMENT-123", **account),
        _statement_xml(
            statement="repeat",
            reference="SECOND-INVOICE",
            servicer="PAYMENT-123",
            **account,
        ),
    )
    candidate = db.list_bank_statement_entries(COMMUNITY)[0]
    page = _follow_assignment(workspace, _assign(workspace, candidate["id"], second))
    assert (
        _assignment_outcome(
            workspace,
            _override(workspace, candidate["id"], second, _review_token(page)),
        )
        == "assigned"
    )


def test_second_structured_creditor_reference_links_payments(workspace):
    _invoice("FIRST-INVOICE")
    second = _invoice("SECOND-INVOICE")

    def with_creditor_refs(xml, invoice_reference):
        xml_refs = (
            f"<Strd><CdtrRefInf><Ref>{invoice_reference}</Ref></CdtrRefInf></Strd>"
            "<Strd><CdtrRefInf><Ref>RF18 5390 0754 7034</Ref></CdtrRefInf></Strd>"
        )
        return xml.replace(b"</RmtInf>", xml_refs.encode() + b"</RmtInf>")

    _upload_xml(
        workspace,
        with_creditor_refs(
            _statement_xml(reference="FIRST-INVOICE", servicer="booking-a"),
            "FIRST-INVOICE",
        ),
        _booking_on(
            with_creditor_refs(
                _statement_xml(
                    statement="late", reference="SECOND-INVOICE", servicer="booking-b"
                ),
                "SECOND-INVOICE",
            ),
            "2026-10-14",
        ),
    )
    candidate = db.list_bank_statement_entries(COMMUNITY)[0]
    assert candidate["duplicate_reason"] == "possible_duplicate_payment"
    page = _follow_assignment(workspace, _assign(workspace, candidate["id"], second))
    assert "gleiche Zahlungsreferenz" in page.text
    _review_token(page)
    assert len(_paid_events()) == 1


def test_identical_repeat_names_booked_invoice_without_an_open_target(workspace):
    first = _invoice("FIRST-INVOICE")
    account = {"account": "CH0000000000000000000", "account_is_iban": True}
    _upload_xml(
        workspace,
        _statement_xml(reference="FIRST-INVOICE", **account),
        _statement_xml(statement="repeat", reference="FIRST-INVOICE", **account),
    )
    candidate = db.list_bank_statement_entries(COMMUNITY)[0]
    page = workspace.get(PAYMENTS)
    card = page.text.split(f'id="statement-{candidate["id"]}"', 1)[1].split(
        "</article>", 1
    )[0]
    assert "Diese Zahlung ist bereits auf Rechnung FIRST-INVOICE gebucht." in card
    assert "/assign" not in card
    assert "Stellen Sie" not in card
    refusal = _follow_assignment(workspace, _assign(workspace, candidate["id"], first))
    assert (
        "Diese Zahlung ist bereits auf Rechnung FIRST-INVOICE gebucht." in refusal.text
    )
    assert 'name="duplicate_review"' not in refusal.text


@pytest.mark.parametrize("earlier", ["pending", "reversal", "booked"])
def test_only_bookable_credit_reports_are_duplicate_evidence(workspace, earlier):
    invoice = _invoice("FIRST-INVOICE")
    report = _statement_xml(
        reference="FIRST-INVOICE",
        reversal=b"<RvslInd>true</RvslInd>" if earlier == "reversal" else b"",
    )
    if earlier == "pending":
        report = report.replace(b"<Cd>BOOK</Cd>", b"<Cd>PDNG</Cd>")
    _upload_xml(workspace, report)
    _upload_xml(
        workspace, _statement_xml(statement="booked-report", reference="FIRST-INVOICE")
    )
    latest, original = db.list_bank_statement_entries(COMMUNITY)
    if earlier == "booked":
        assert latest["duplicate_reason"] == "possible_duplicate_payment"
        assert _entry_booking(original["id"]) == ("matched", invoice)
    else:
        assert latest["duplicate_reason"] is None
        assert _entry_booking(latest["id"]) == ("matched", invoice)
    assert len(_paid_events()) == 1
