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
PAYMENTS = f"/leg/community/{COMMUNITY}/payments"


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
    assert location.path == PAYMENTS
    assert location.fragment == "payments"
    assert len(query["assignment"]) == 1
    page = client.get(response.location)
    assert page.status_code == 200
    assert f'href="{PAYMENTS}" aria-current="page"' in " ".join(page.text.split())
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
    page = workspace.get(PAYMENTS)
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
        page = workspace.get(PAYMENTS)
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
        failure.setattr(dashboard, "leg_payments_view", unavailable)
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
    page = workspace.get(PAYMENTS)

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
    entries = db.list_bank_statement_entries(COMMUNITY, limit=201)
    assert len(entries) == 201
    assert all(row["match_decision"] == "reversal" for row in entries[:-1])
    assert entries[-1]["id"] == older["id"]
    page = workspace.get(PAYMENTS)
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
    assert "Fictional contact only" not in workspace.get(PAYMENTS).text


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
    first_page = workspace.get(PAYMENTS)
    assert "old unresolved reference" not in first_page.text
    assert "Ältere offene Buchungen" in first_page.text
    older_path = f"{PAYMENTS}?statement_before={older['id'] + 1}#payments"
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


def _overlapping_overrides(app_module, monkeypatch, first, second):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic, sleep

    from store import billing

    entered, release = Event(), Event()
    first_pid = None
    duplicate_check = billing._bank_payment_duplicate

    def pause_first(cur, community_id, entry, *, hold_only=False):
        nonlocal first_pid
        result = duplicate_check(cur, community_id, entry, hold_only=hold_only)
        if first_pid is None:
            first_pid = cur.connection.get_backend_pid()
            entered.set()
            assert release.wait(timeout=20)
        return result

    monkeypatch.setattr(billing, "_bank_payment_duplicate", pause_first)

    def confirm(booking):
        with app_module.web.test_client() as client:
            _set_session(client, ACTOR)
            return _assignment_outcome(client, _override(client, *booking))

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_request = pool.submit(confirm, first)
        try:
            assert entered.wait(timeout=10), (
                "first confirmation did not reach its duplicate check"
            )
            second_request = pool.submit(confirm, second)
            with db.get_connection() as conn, conn.cursor() as cur:
                deadline = monotonic() + 10
                while True:
                    cur.execute(
                        """SELECT EXISTS (
                               SELECT 1 FROM pg_locks
                               WHERE locktype='advisory' AND NOT granted
                                 AND %s=ANY(pg_blocking_pids(pid))
                           ) AS waiting""",
                        (first_pid,),
                    )
                    if cur.fetchone()["waiting"]:
                        break
                    assert not second_request.done() and monotonic() < deadline, (
                        "second confirmation did not wait on the first confirmation's LEG lock"
                    )
                    sleep(0.01)
        finally:
            release.set()
        return [first_request.result(timeout=10), second_request.result(timeout=10)]


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


def test_override_shows_and_rechecks_the_complete_set(
    workspace, app_module, monkeypatch
):
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
    for fields in (
        {"confirm_distinct": ""},
        {"csrf_token": "invalid", "confirm_distinct": "yes"},
    ):
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

    booking = (entry, candidate_invoice, _review_token(refreshed))
    assert _overlapping_overrides(app_module, monkeypatch, booking, booking) == [
        "assigned",
        "assigned",
    ]
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


@pytest.mark.parametrize(
    "case", ["distinct-banks", "iban-and-other", "identical", "older-invoice-page"]
)
def test_likely_repeat_needs_audited_dashboard_review(workspace, monkeypatch, case):
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
            **(iban if case in {"identical", "older-invoice-page"} else {}),
        )
    if case == "older-invoice-page":
        import dashboard

        monkeypatch.setattr(dashboard, "_BILLING_PAGE_SIZE", 1)
        _invoice("NEWER-OPEN-INVOICE")
    _upload_xml(workspace, original, repeat)
    candidate, prior = db.list_bank_statement_entries(COMMUNITY)
    assert _entry_booking(prior["id"]) == ("matched", first)
    assert candidate["duplicate_reason"] == "possible_duplicate_payment"
    if case == "older-invoice-page":
        assert f'<option value="{second}">' not in workspace.get(PAYMENTS).text
    page = _follow_assignment(workspace, _assign(workspace, candidate["id"], second))
    assert "Sehr wahrscheinlich dieselbe Zahlung" in page.text
    card = page.text.split(f'id="statement-{candidate["id"]}"', 1)[1].split(
        "</article>", 1
    )[0]
    assert "Wahrscheinlich bereits auf Rechnung FIRST-INVOICE gebucht." in card
    token = _review_token(page)
    for _ in range(2):
        assert (
            _assignment_outcome(
                workspace, _override(workspace, candidate["id"], second, token)
            )
            == "assigned"
        )
    assert len(_paid_events()) == 2
    audit = next(
        event
        for event in db.list_community_invoice_events(COMMUNITY)
        if event["invoice_id"] == second and event["event_type"] == "paid"
    )
    assert audit["payment_match"]["duplicate_override"]["reviewed_entry_ids"] == [
        prior["id"]
    ]


@pytest.mark.parametrize(
    ("source_reference", "other_reference", "held", "structured"),
    [
        ("000000000000000000000000001", "000000000000000000000000002", False, True),
        ("Lease AB C", "Lease A BC", False, False),
        ("Unstructured lease payment", "Unstructured lease payment", False, False),
        ("LEG-2026-000042", "leg 2026 000042", True, True),
        ("LEG-2026-000042", "leg 2026 000042", True, False),
        ("000000000000000000000000001", "00000 00000 00000 00000 0000001", True, True),
        ("RF18539007547034", "rf18 5390 0754 7034", True, True),
    ],
)
def test_same_amount_outside_date_window_requires_reference_evidence(
    workspace, source_reference, other_reference, held, structured
):
    first = _invoice(
        source_reference if source_reference.startswith("LEG-") else "FIRST-INVOICE"
    )
    second = _invoice("SECOND-INVOICE")

    def statement(reference, **kwargs):
        xml = _statement_xml(reference=reference, **kwargs)
        if structured:
            xml = xml.replace(b"<Ustrd>", b"<Strd><CdtrRefInf><Ref>").replace(
                b"</Ustrd>", b"</Ref></CdtrRefInf></Strd>"
            )
        return xml

    _upload_xml(workspace, statement(source_reference, servicer="booking-a"))
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
            statement(
                other_reference,
                statement="second-export",
                servicer="booking-b",
            ),
            "2026-10-14" if structured and not held else "2026-09-24",
        ),
    )
    candidate = db.list_bank_statement_entries(COMMUNITY)[0]
    assert candidate["payment_reference"] == other_reference
    response = _assign(workspace, candidate["id"], second)
    if held:
        assert candidate["duplicate_reason"] == "possible_duplicate_payment"
        review = _follow_assignment(workspace, response)
        _review_token(review)
        assert "gleiche Zahlungsreferenz" in review.text
        assert len(_paid_events()) == 1
    else:
        assert candidate["duplicate_reason"] is None
        assert _assignment_outcome(workspace, response) == "assigned"
        assert len(_paid_events()) == 2


def _with_parent_reference(xml, reference):
    return xml.replace(
        b"</BookgDt>", f"</BookgDt><AcctSvcrRef>{reference}</AcctSvcrRef>".encode()
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
    workspace, app_module, monkeypatch
):
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

    assert _overlapping_overrides(app_module, monkeypatch, *bookings) == [
        "assigned",
        "refused",
    ]
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


def _one_upload(*statements):
    first, *others = statements
    extra = b"".join(
        xml[xml.index(b"<Stmt>") : xml.index(b"</Stmt>") + len(b"</Stmt>")]
        for xml in others
    )
    return first.replace(b"</BkToCstmrStmt>", extra + b"</BkToCstmrStmt>")


def _accounts_sharing_a_reference(second_amount="9.67", second_date="2026-09-14"):
    return _one_upload(
        _statement_xml(
            reference="FIRST-INVOICE",
            servicer="shared-entry",
            account="CH0000000000000000001",
            account_is_iban=True,
        ),
        _booking_on(
            _statement_xml(
                statement="second-account",
                reference="SECOND-INVOICE",
                servicer="shared-entry",
                account="CH0000000000000000002",
                account_is_iban=True,
            ).replace(b"9.67", second_amount.encode()),
            second_date,
        ),
    )


def _post_statement(client, body):
    from io import BytesIO

    _set_session(client, ADMIN)
    response = client.post(
        f"{BILLING}/statements",
        data={"csrf_token": "csrf-secret", "statement": (BytesIO(body), "same.xml")},
    )
    _set_session(client, ACTOR)
    return response


def test_accounts_sharing_a_local_reference_import_from_one_upload(workspace):
    first, second = _invoice("FIRST-INVOICE"), _invoice("SECOND-INVOICE")
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("ALTER TABLE invoices DISABLE TRIGGER USER")
        cur.execute("UPDATE invoices SET gross_chf=12.00 WHERE id=%s", (second,))
        cur.execute("ALTER TABLE invoices ENABLE TRIGGER USER")
    upload = _accounts_sharing_a_reference(second_amount="12.00")

    for outcome in ("imported", "duplicate"):
        response = _post_statement(workspace, upload)
        assert response.status_code == 302
        assert response.location.endswith(f"?statement={outcome}")

    rows = db.list_bank_statement_entries(COMMUNITY)
    assert sorted(
        (row["bank_account"], row["entry_reference"], row["bank_references"])
        for row in rows
    ) == [
        ("CH0000000000000000001", "shared-entry", ["shared-entry"]),
        ("CH0000000000000000002", "shared-entry", ["shared-entry"]),
    ]
    assert len({row["statement_import_id"] for row in rows}) == 1
    assert sorted(_entry_booking(row["id"]) for row in rows) == sorted(
        [("matched", first), ("matched", second)]
    )
    assert len(_paid_events()) == 2


@pytest.mark.parametrize("second_date", ["2026-09-14", "2026-09-24"])
def test_duplicate_guard_holds_equal_credits_on_two_accounts(workspace, second_date):
    first, second = _invoice("FIRST-INVOICE"), _invoice("SECOND-INVOICE")
    upload = _accounts_sharing_a_reference(second_date=second_date)
    assert _post_statement(workspace, upload).status_code == 302
    newer, earlier = db.list_bank_statement_entries(COMMUNITY)
    assert [newer["bank_account"], earlier["bank_account"]] == [
        "CH0000000000000000002",
        "CH0000000000000000001",
    ]
    assert [newer["duplicate_reason"], earlier["duplicate_reason"]] == [
        "possible_duplicate_payment"
    ] * 2
    assert not _paid_events()
    review = _follow_assignment(workspace, _assign(workspace, earlier["id"], first))
    matches = review.text.split('id="duplicate-review-matches"', 1)[1].split(
        "</ul>", 1
    )[0]
    assert "second-account" in matches
    assert "gleiche Bankreferenz" in matches
    assert (
        _assignment_outcome(
            workspace, _override(workspace, earlier["id"], first, _review_token(review))
        )
        == "assigned"
    )
    review = _follow_assignment(workspace, _assign(workspace, newer["id"], second))
    assert "gleiche Bankreferenz" in review.text
    same_day = second_date == "2026-09-14"
    assert ("Sehr wahrscheinlich dieselbe Zahlung" in review.text) is same_day
    assert len(_paid_events()) == 1


def test_long_servicer_details_cannot_break_the_account_index(workspace):
    from hashlib import sha256

    def text(seed):
        # Varied multibyte text, so PostgreSQL cannot compress the index row.
        digest = "".join(sha256(f"{seed}:{n}".encode()).hexdigest() for n in range(5))
        return "".join(
            chr(0x4E00 + int(digest[i : i + 4], 16) % 0x5000) for i in range(0, 280, 4)
        )

    def statement(name, account, reference):
        address = "".join(
            f"<AdrLine>{text(f'{name}:{line}')}</AdrLine>" for line in range(7)
        )
        branch = "".join(
            f"<AdrLine>{text(f'{name}:branch:{line}')}</AdrLine>" for line in range(7)
        )
        servicer = (
            f"<Svcr><FinInstnId><PstlAdr>{address}</PstlAdr></FinInstnId>"
            f"<BrnchId><PstlAdr>{branch}</PstlAdr></BrnchId></Svcr></Acct>"
        )
        return _statement_xml(
            statement=name,
            reference=reference,
            servicer="shared-entry",
            account=account,
        ).replace(b"</Acct>", servicer.encode())

    upload = _one_upload(
        statement("first-account", "1000", "FIRST-INVOICE"),
        statement("second-account", "1000", "SECOND-INVOICE"),
    )
    response = _post_statement(workspace, upload)
    assert response.status_code == 302
    assert response.location.endswith("?statement=imported")
    rows = db.list_bank_statement_entries(COMMUNITY)
    assert [row["entry_reference"] for row in rows] == ["shared-entry"] * 2
    assert len({row["bank_account"] for row in rows}) == 2


def test_upgrade_scopes_entry_references_by_account_without_a_rewrite(workspace):
    _invoice("FIRST-INVOICE")
    _upload_xml(
        workspace, _statement_xml(reference="FIRST-INVOICE", servicer="legacy-entry")
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        # Restore the 32e5a836 shape: entry references unique per import only.
        cur.execute("DROP INDEX IF EXISTS uq_bank_statement_entries_account_reference")
        cur.execute("ALTER TABLE bank_statement_entries DROP COLUMN bank_account")
        cur.execute("""
            ALTER TABLE bank_statement_entries
            ADD CONSTRAINT bank_statement_entries_statement_import_id_entry_reference_key
            UNIQUE (statement_import_id, entry_reference)
        """)
        cur.execute("SELECT pg_relation_filenode('bank_statement_entries') AS node")
        before = cur.fetchone()["node"]
    db.create_tables()
    db.create_tables()
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_relation_filenode('bank_statement_entries') AS node")
        assert cur.fetchone()["node"] == before
        cur.execute("""
            SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint
            WHERE conrelid = 'bank_statement_entries'::regclass AND contype = 'u'
        """)
        assert cur.fetchall() == []
        cur.execute("""
            SELECT indexdef FROM pg_indexes
            WHERE tablename = 'bank_statement_entries' AND indexdef LIKE 'CREATE UNIQUE%%'
              AND indexname <> 'bank_statement_entries_pkey'
        """)
        assert [row["indexdef"] for row in cur.fetchall()] == [
            (
                "CREATE UNIQUE INDEX uq_bank_statement_entries_account_reference "
                "ON public.bank_statement_entries USING btree "
                "(statement_import_id, bank_account, entry_reference)"
            )
        ]
    (legacy,) = db.list_bank_statement_entries(COMMUNITY)
    assert (legacy["bank_account"], legacy["entry_reference"]) == ("", "legacy-entry")
    assert _post_statement(workspace, _accounts_sharing_a_reference()).status_code == (
        302
    )
    assert len(db.list_bank_statement_entries(COMMUNITY)) == 3


def test_bank_references_match_across_kinds_and_dates(workspace):
    first = _invoice("FIRST-INVOICE")
    second = _invoice("SECOND-INVOICE")
    original = _statement_xml(reference="FIRST-INVOICE").replace(
        b"AcctSvcrRef", b"NtryRef"
    )
    repeat = _booking_on(
        _statement_xml(statement="repeat", reference="SECOND-INVOICE"), "2026-09-24"
    )
    _upload_xml(workspace, original, repeat)
    candidate, prior = db.list_bank_statement_entries(COMMUNITY)
    assert _entry_booking(prior["id"]) == ("matched", first)
    page = _follow_assignment(workspace, _assign(workspace, candidate["id"], second))
    assert "gleiche Bankreferenz" in page.text
    assert (
        _assignment_outcome(
            workspace,
            _override(workspace, candidate["id"], second, _review_token(page)),
        )
        == "assigned"
    )
    assert len(_paid_events()) == 2


@pytest.mark.parametrize("repeat_date", ["2026-09-14", "2026-09-24"])
def test_bank_reference_comparison_ignores_surrounding_whitespace(
    workspace, repeat_date
):
    _invoice("FIRST-INVOICE")
    second = _invoice("SECOND-INVOICE")
    account = {"account": "CH0000000000000000000", "account_is_iban": True}
    _upload_xml(
        workspace,
        _statement_xml(reference="FIRST-INVOICE", servicer=" PAYMENT-123 ", **account),
        _booking_on(
            _statement_xml(
                statement="repeat",
                reference="SECOND-INVOICE",
                servicer="PAYMENT-123",
                **account,
            ),
            repeat_date,
        ),
    )
    candidate, prior = db.list_bank_statement_entries(COMMUNITY)
    assert prior["bank_references"] == [" PAYMENT-123 "]
    assert candidate["duplicate_reason"] == "possible_duplicate_payment"
    page = _follow_assignment(workspace, _assign(workspace, candidate["id"], second))
    review = page.text.split('id="duplicate-review-matches"', 1)[1].split("</ul>", 1)[0]
    same_day = repeat_date == "2026-09-14"
    assert "gleiche Bankreferenz" in review
    assert ("Sehr wahrscheinlich dieselbe Zahlung" in review) is same_day
    assert (
        "Wahrscheinlich bereits auf Rechnung FIRST-INVOICE gebucht." in page.text
    ) is same_day
    _review_token(page)
    assert len(_paid_events()) == 1


@pytest.mark.parametrize("padding", ["\u00a0", "\u2007", "\u202f"])
def test_no_break_space_padding_shows_label_and_card_hint(workspace, padding):
    _invoice("FIRST-INVOICE")
    second = _invoice("SECOND-INVOICE")
    account = {"account": "CH0000000000000000000", "account_is_iban": True}
    padded = f"{padding}PAYMENT-123{padding}"
    _upload_xml(
        workspace,
        _statement_xml(reference="FIRST-INVOICE", servicer=padded, **account),
        _statement_xml(
            statement="repeat",
            reference="SECOND-INVOICE",
            servicer="PAYMENT-123",
            **account,
        ),
    )
    candidate, prior = db.list_bank_statement_entries(COMMUNITY)
    assert prior["bank_references"] == [padded]
    page = _follow_assignment(workspace, _assign(workspace, candidate["id"], second))
    review = page.text.split('id="duplicate-review-matches"', 1)[1].split("</ul>", 1)[0]
    card = page.text.split(f'id="statement-{candidate["id"]}"', 1)[1].split(
        "</article>", 1
    )[0]
    assert "Sehr wahrscheinlich dieselbe Zahlung" in review
    assert "Wahrscheinlich bereits auf Rechnung FIRST-INVOICE gebucht." in card
    _review_token(page)
    assert len(_paid_events()) == 1


def _drop_bank_reference_columns(cur):
    # Restore the persisted row shape from 7b47c415 before running the upgrade.
    cur.execute("""
        ALTER TABLE bank_statement_entries
            DROP COLUMN bank_references, DROP COLUMN IF EXISTS legacy_bank_references,
            DROP COLUMN creditor_references, DROP COLUMN source_statement_reference,
            DROP COLUMN debtor_name, DROP COLUMN remittance_information,
            DROP COLUMN duplicate_reason
    """)


@pytest.mark.parametrize("bank_reference", ["different-reference", "statement:1:1"])
def test_legacy_tokens_hold_for_review_without_claiming_a_likely_repeat(
    workspace, bank_reference
):
    for name, booking_date in (
        ("FIRST-INVOICE", "2026-09-14"),
        ("SECOND-INVOICE", "2026-09-24"),
        ("THIRD-INVOICE", "2026-09-24"),
    ):
        invoice = _invoice(name)
        _upload_xml(
            workspace,
            _booking_on(
                _statement_xml(statement="", reference=name, servicer=""), booking_date
            ),
        )
        entry = db.list_bank_statement_entries(COMMUNITY)[0]
        assert entry["entry_reference"] == "statement:1:1"
        if entry["match_decision"] != "matched":
            page = _follow_assignment(
                workspace, _assign(workspace, entry["id"], invoice)
            )
            assert (
                _assignment_outcome(
                    workspace,
                    _override(workspace, entry["id"], invoice, _review_token(page)),
                )
                == "assigned"
            )
    with db.get_connection() as conn, conn.cursor() as cur:
        _drop_bank_reference_columns(cur)
    db.create_tables()
    db.create_tables()
    target = _invoice("TARGET-INVOICE")
    _upload_xml(
        workspace,
        _statement_xml(
            statement="new-report", reference="TARGET-INVOICE", servicer=bank_reference
        ),
    )
    entry = db.list_bank_statement_entries(COMMUNITY)[0]
    assert entry["duplicate_reason"] == "possible_duplicate_payment"
    page = _follow_assignment(workspace, _assign(workspace, entry["id"], target))
    _review_token(page)
    review = page.text.split('id="duplicate-review-matches"', 1)[1].split("</ul>", 1)[0]
    for name in ("FIRST-INVOICE", "SECOND-INVOICE", "THIRD-INVOICE"):
        assert name in review
    assert "Sehr wahrscheinlich dieselbe Zahlung" not in review
    assert "Wahrscheinlich bereits auf Rechnung" not in page.text
    assert len(_paid_events()) == 3


@pytest.mark.parametrize(
    ("statement", "stored_reference", "kind", "expected_references", "repeat"),
    [
        ("s", "shared-bank", "single", ["shared-bank"], "shared-bank"),
        ("s", "WIRE:123", "single", ["WIRE:123", "WIRE"], "WIRE:123"),
        ("s", "WIRE:123:456", "single", ["WIRE:123:456", "WIRE:123", "WIRE"], "WIRE"),
        ("s", "s:1:1", "generated", ["s:1:1", "s:1", "s"], "s:1:1"),
        ("s", "batch-bank:1", "batch", ["batch-bank:1", "batch-bank"], "batch-bank"),
        ("s", "\t\n", "whitespace", [], "\t\n"),
        ("s", "notprovided", "single", [], "notprovided"),
        ("s", "WIRE:123", "declared-single", ["WIRE:123", "WIRE"], "WIRE:123"),
        (
            "WIRE",
            "WIRE:123:456",
            "single",
            ["WIRE:123:456", "WIRE:123", "WIRE"],
            "WIRE:123:456",
        ),
        (
            "ARCHIVE:42",
            "ARCHIVE:42:1:1",
            "single",
            ["ARCHIVE:42:1:1", "ARCHIVE:42:1", "ARCHIVE:42"],
            "ARCHIVE:42:1:1",
        ),
    ],
)
def test_upgrade_preserves_only_legacy_bank_references(
    workspace, statement, stored_reference, kind, expected_references, repeat
):
    first = _invoice("FIRST-INVOICE")
    second = _invoice("SECOND-INVOICE")
    servicer = {"generated": "", "batch": "batch-bank", "whitespace": ""}.get(
        kind, stored_reference
    )
    original = _statement_xml(
        statement=statement, reference="FIRST-INVOICE", servicer=servicer
    )
    if kind == "batch":
        original = (
            original.replace(b"9.67", b"10.67", 1)
            .replace(b"<TxDtls>", b'<TxDtls><Amt Ccy="CHF">9.67</Amt>')
            .replace(
                b"</NtryDtls>",
                b'<TxDtls><Amt Ccy="CHF">1.00</Amt></TxDtls></NtryDtls>',
            )
        )
    if kind == "declared-single":
        original = original.replace(
            b"<NtryDtls><TxDtls>",
            b'<NtryDtls><Btch><NbOfTxs>1</NbOfTxs></Btch><TxDtls><Amt Ccy="CHF">9.67</Amt>',
        )
    _upload_xml(workspace, original)
    earlier = next(
        row
        for row in db.list_bank_statement_entries(COMMUNITY)
        if row["invoice_id"] == first
    )
    assert _entry_booking(earlier["id"]) == ("matched", first)
    if kind in {"batch", "declared-single"}:
        assert earlier["amount_basis"] == "reconciled_batch"
    with db.get_connection() as conn, conn.cursor() as cur:
        if kind == "whitespace":
            cur.execute(
                "UPDATE bank_statement_entries SET entry_reference=%s WHERE id=%s",
                (stored_reference, earlier["id"]),
            )
        _drop_bank_reference_columns(cur)
    db.create_tables()
    db.create_tables()

    _upload_xml(
        workspace,
        _booking_on(
            _statement_xml(
                statement="reissued",
                reference="SECOND-INVOICE",
                servicer=repeat,
            ),
            "2026-09-18",
        ),
    )
    candidate, *prior_rows = db.list_bank_statement_entries(COMMUNITY)
    prior = next(row for row in prior_rows if row["id"] == earlier["id"])
    assert prior["entry_reference"] == stored_reference
    assert _entry_booking(prior["id"]) == ("matched", first)
    if expected_references:
        assert candidate["duplicate_reason"] == "possible_duplicate_payment"
        assert _entry_booking(candidate["id"]) == ("mismatch", None)
        page = _follow_assignment(
            workspace, _assign(workspace, candidate["id"], second)
        )
        _review_token(page)
        assert "gleiche Bankreferenz" in page.text
        assert len(_paid_events()) == 1
    else:
        assert _entry_booking(candidate["id"]) == ("matched", second)
        assert len(_paid_events()) == 2
    assert prior["bank_references"] == []
    assert sorted(prior["legacy_bank_references"]) == sorted(expected_references)


def test_schema_reruns_keep_current_statement_references(workspace):
    _invoice("FIRST-INVOICE")
    _upload_xml(
        workspace,
        _with_parent_reference(
            _statement_xml(reference="FIRST-INVOICE", servicer="bank:7:8"), "parent"
        ),
    )
    db.create_tables()
    db.create_tables()
    (entry,) = db.list_bank_statement_entries(COMMUNITY)
    assert entry["bank_references"] == ["bank:7:8", "parent"]
    assert entry["legacy_bank_references"] == []


@pytest.mark.parametrize("shared", ["RF18 5390 0754 7034", "FIRST-INVOICE"])
def test_second_structured_creditor_reference_links_payments(workspace, shared):
    _invoice("FIRST-INVOICE")
    second = _invoice("SECOND-INVOICE")

    def with_creditor_refs(xml, invoice_reference):
        xml_refs = (
            f"<Strd><CdtrRefInf><Ref>{invoice_reference}</Ref></CdtrRefInf></Strd>"
            f"<Strd><CdtrRefInf><Ref>{shared}</Ref></CdtrRefInf></Strd>"
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
    card = page.text.split(f'id="statement-{candidate["id"]}"', 1)[1].split(
        "</article>", 1
    )[0]
    assert shared in card
    assert "gleiche Zahlungsreferenz" in page.text
    _review_token(page)
    assert len(_paid_events()) == 1


def test_identical_repeat_names_booked_invoice_without_an_open_target(workspace):
    _invoice("FIRST-INVOICE")
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
    assert "Wahrscheinlich bereits auf Rechnung FIRST-INVOICE gebucht." in card
    assert "/assign" not in card
    assert len(_paid_events()) == 1


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


def test_duplicate_review_includes_booked_payment_reached_through_a_report(workspace):
    _invoice("FIRST-INVOICE")
    target = _invoice("SECOND-INVOICE")
    _upload_xml(
        workspace,
        _statement_xml(reference="FIRST-INVOICE", servicer="bank-a"),
        _with_parent_reference(
            _statement_xml(statement="bridge", reference="report", servicer="bank-b"),
            "bank-a",
        ),
        _booking_on(
            _statement_xml(
                statement="candidate", reference="SECOND-INVOICE", servicer="bank-b"
            ),
            "2026-09-24",
        ),
    )
    candidate, bridge, booked = db.list_bank_statement_entries(COMMUNITY)
    entry = candidate["id"]
    page = _follow_assignment(workspace, _assign(workspace, entry, target))
    review = page.text.split('id="duplicate-review-matches"', 1)[1].split("</ul>", 1)[0]
    assert "Sehr wahrscheinlich dieselbe Zahlung" in review
    assert review.index("FIRST-INVOICE") < review.index("bridge")
    token = _review_token(page)
    _upload_xml(
        workspace,
        _booking_on(
            _statement_xml(
                statement="new-near-bridge",
                reference="additional report",
                servicer="bank-d",
            ),
            "2026-09-15",
        ),
    )
    changed = _follow_assignment(workspace, _override(workspace, entry, target, token))
    assert (
        "new-near-bridge"
        in changed.text.split('id="duplicate-review-matches"', 1)[1].split("</ul>", 1)[
            0
        ]
    )
    assert len(_paid_events()) == 1
    assert (
        _assignment_outcome(
            workspace, _override(workspace, entry, target, _review_token(changed))
        )
        == "assigned"
    )
    latest = db.list_bank_statement_entries(COMMUNITY)[0]
    paid = next(
        event
        for event in db.list_community_invoice_events(COMMUNITY)
        if event["invoice_id"] == target and event["event_type"] == "paid"
    )
    assert paid["payment_match"]["duplicate_override"]["reviewed_entry_ids"] == sorted(
        [booked["id"], bridge["id"], latest["id"]]
    )


def test_untyped_bank_reference_links_a_repeat_outside_the_date_window(workspace):
    _invoice("FIRST-INVOICE")
    _invoice("SECOND-INVOICE")
    _upload_xml(
        workspace, _statement_xml(reference="FIRST-INVOICE", servicer="shared-bank")
    )
    [earlier] = db.list_bank_statement_entries(COMMUNITY)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE bank_statement_entries SET bank_references = ARRAY['shared-bank'] WHERE id=%s",
            (earlier["id"],),
        )
    repeat = _statement_xml(
        statement="later", reference="SECOND-INVOICE", servicer="shared-bank"
    )
    _upload_xml(workspace, _booking_on(repeat, "2026-10-14"))
    latest = db.list_bank_statement_entries(COMMUNITY)[0]
    assert latest["duplicate_reason"] == "possible_duplicate_payment"
    assert len(_paid_events()) == 1


STAND_IN = "Bankreferenz (AcctSvcrRef) anstelle der Zahlungsreferenz"


def test_preparer_assigns_credit_without_reference_by_servicer_reference(workspace):
    from datetime import date

    invoice = _invoice()
    _upload_xml(workspace, _statement_xml(reference="", servicer="bank-entry-1"))
    (entry,) = db.list_bank_statement_entries(COMMUNITY)
    assert entry["payment_reference"] == ""
    card = workspace.get(PAYMENTS).text.split(f'id="statement-{entry["id"]}"', 1)[1]
    card = card.split("</article>", 1)[0]
    assert f"/statements/{entry['id']}/assign" in card
    assert STAND_IN in card and "bank-entry-1" in card

    for _ in range(2):
        response = _assign(workspace, entry["id"], invoice)
        assert _assignment_outcome(workspace, response) == "assigned"

    assert _entry_booking(entry["id"]) == ("matched", invoice)
    (paid,) = [
        event
        for event in db.list_community_invoice_events(COMMUNITY)
        if event["event_type"] == "paid"
    ]
    assert (paid["actor_id"], paid["actor"]["actor_kind"]) == (ACTOR, "human")
    assert (paid["reference"], paid["effective_date"]) == (
        "bank-entry-1",
        date(2026, 9, 14),
    )
    assert paid["payment_match"] == {
        "entry_id": entry["id"],
        "decision": "matched",
        "reference_stand_in": "AcctSvcrRef",
    }
    assert sorted(
        event["event_type"]
        for event in db.list_community_operator_events(
            COMMUNITY, event_types=("invoice.paid", "payment.match.confirmed")
        )
    ) == ["invoice.paid", "payment.match.confirmed"]
    detail = workspace.get(f"/leg/community/{COMMUNITY}/invoices/{invoice}")
    assert detail.status_code == 200
    audit = detail.text.split("Audit-Verlauf", 1)[1]
    assert f"{STAND_IN}: bank-entry-1" in audit


def test_repeated_credit_without_reference_needs_duplicate_review(workspace):
    first, second = _invoice("FIRST-INVOICE"), _invoice("SECOND-INVOICE")
    _upload_xml(
        workspace,
        _statement_xml(reference=""),
        _statement_xml(statement="repeat", reference=""),
    )
    repeat, original = db.list_bank_statement_entries(COMMUNITY)
    assert repeat["duplicate_reason"] == "possible_duplicate_payment"
    for row, invoice in ((original, first), (repeat, second)):
        page = _follow_assignment(workspace, _assign(workspace, row["id"], invoice))
        assert "Mögliche Doppelzahlung." in page.text
        assert len(_paid_events()) == int(row is repeat)
        response = _override(workspace, row["id"], invoice, _review_token(page))
        assert _assignment_outcome(workspace, response) == "assigned"

    audit = {
        event["invoice_id"]: event
        for event in db.list_community_invoice_events(COMMUNITY)
        if event["event_type"] == "paid"
    }
    for row, invoice, other in ((original, first, repeat), (repeat, second, original)):
        assert audit[invoice]["reference"] == "bank-entry-1"
        assert audit[invoice]["payment_match"]["reference_stand_in"] == "AcctSvcrRef"
        assert audit[invoice]["payment_match"]["duplicate_override"][
            "reviewed_entry_ids"
        ] == [other["id"]]


def test_credit_without_reference_holds_a_matching_automatic_booking(workspace):
    _invoice("FIRST-INVOICE")
    _upload_xml(
        workspace,
        _statement_xml(reference="", servicer="bank-a"),
        _statement_xml(
            statement="referenced", reference="FIRST-INVOICE", servicer="bank-b"
        ),
    )

    referenced, unreferenced = db.list_bank_statement_entries(COMMUNITY)

    assert referenced["duplicate_reason"] == "possible_duplicate_payment"
    assert unreferenced["servicer_reference"] == "bank-a"
    assert not _paid_events()


def test_credit_without_any_bank_reference_stays_refused(workspace):
    invoice = _invoice()
    _upload_xml(workspace, _statement_xml(reference="", servicer=""))
    (entry,) = db.list_bank_statement_entries(COMMUNITY)
    assert entry["servicer_reference"] is None

    page = _follow_assignment(workspace, _assign(workspace, entry["id"], invoice))

    feedback = page.text.split('id="assignment-feedback" role="alert"', 1)[1]
    assert "Die Zahlung hat keine Referenz." in feedback.split("</p>", 1)[0]
    assert _entry_booking(entry["id"]) == ("unmatched", None)
    assert not _paid_events()


@pytest.mark.parametrize("case", ["replay", "dry_run", "other_invoice"])
def test_operator_cannot_replay_a_workspace_assignment_without_reference(
    workspace, case
):
    from unittest.mock import patch

    invoice = _invoice()
    other = _invoice("LEG-PAYMENT-2")
    _upload_xml(workspace, _statement_xml(reference=""))
    (entry,) = db.list_bank_statement_entries(COMMUNITY)
    response = _assign(workspace, entry["id"], invoice)
    assert _assignment_outcome(workspace, response) == "assigned"
    db.create_operator_api_client(
        COMMUNITY,
        ADMIN,
        "payments.mutate",
        ["payments.mutate"],
        hashlib.sha256(b"fictional-mutate").hexdigest(),
        None,
    )
    before = (
        db.list_bank_statement_entries(COMMUNITY),
        db.list_community_invoice_events(COMMUNITY),
    )

    with patch(
        "operator_api.db.operator_creator_is_confirmed_admin", return_value=True
    ):
        response = workspace.post(
            f"/api/operator/v1/communities/{COMMUNITY}/payments/matches/"
            f"{entry['id']}/confirm" + ("?dry_run=true" if case == "dry_run" else ""),
            headers={
                "Authorization": "Bearer fictional-mutate",
                # The workspace's own key must not let an operator claim its booking.
                "Idempotency-Key": f"dashboard-payment-{entry['id']}",
            },
            json={"invoice_id": other if case == "other_invoice" else invoice},
        )

    assert (response.status_code, response.json["code"]) == (409, "missing_reference")
    assert "event_id" not in response.json
    assert (
        db.list_bank_statement_entries(COMMUNITY),
        db.list_community_invoice_events(COMMUNITY),
    ) == before


def test_payments_is_the_workspace_for_import_and_review(workspace):
    _set_session(workspace, ADMIN)
    response = workspace.get(PAYMENTS)
    assert response.status_code == 200
    assert "<title>Zahlungen | OpenLEG</title>" in response.text
    assert f'href="{PAYMENTS}" aria-current="page"' in " ".join(response.text.split())
    assert "camt.053 oder camt.054" in response.text
    assert f'action="{BILLING}/statements"' in response.text
    assert 'name="statement"' in response.text
    assert "Noch kein Kontoauszug importiert." in response.text
    assert "Keine Einträge warten auf eine Zuordnung." in response.text
    assert "Keine offenen Rechnungen." in response.text
    assert 'name="statement"' not in workspace.get(BILLING).text
    legacy = workspace.get(BILLING + "?section=payments&statement_before=12")
    assert legacy.location == PAYMENTS + "?statement_before=12#payments"


def test_payments_summarizes_upload_replay_and_assignment(workspace):
    from io import BytesIO

    _invoice("AUTO-BOOKED")
    manual = _invoice("FOR-MANUAL-ASSIGNMENT")
    auto_xml = _statement_xml(reference="AUTO-BOOKED")
    pending_xml = _booking_on(
        _statement_xml(reference="NEEDS-ASSIGNMENT", servicer="bank-entry-2"),
        "2026-10-14",
    )
    end = auto_xml.index(b"</Ntry>") + len(b"</Ntry>")
    pending_start = pending_xml.index(b"<Ntry>")
    pending_end = pending_xml.index(b"</Ntry>") + len(b"</Ntry>")
    statement = auto_xml[:end] + pending_xml[pending_start:pending_end] + auto_xml[end:]

    def upload():
        _set_session(workspace, ADMIN)
        return workspace.post(
            f"{BILLING}/statements",
            data={
                "csrf_token": "csrf-secret",
                "statement": (BytesIO(statement), "summary.xml"),
            },
        )

    response = upload()
    assert response.status_code == 302
    assert urlsplit(response.location).path == PAYMENTS
    first = workspace.get(response.location)
    assert "Der Kontoauszug wurde importiert und abgeglichen." in first.text
    assert "summary.xml" in first.text
    for label, count in (
        ("Importierte Einträge", 2),
        ("Automatisch verbucht", 1),
        ("Manuell zugeordnet", 0),
        ("Warten auf Zuordnung", 1),
        ("Doppelte Einträge übersprungen", 0),
    ):
        assert f"<dt>{label}</dt> <dd>{count}</dd>" in " ".join(first.text.split())

    duplicate = workspace.get(upload().location)
    assert "Dieser Kontoauszug wurde bereits importiert." in duplicate.text
    assert "<dt>Doppelte Einträge übersprungen</dt> <dd>2</dd>" in " ".join(
        duplicate.text.split()
    )
    assert len(_paid_events()) == 1

    pending = next(
        row
        for row in db.list_bank_statement_entries(COMMUNITY)
        if row["payment_reference"] == "NEEDS-ASSIGNMENT"
    )
    _set_session(workspace, ACTOR)
    after_assignment = _follow_assignment(
        workspace, _assign(workspace, pending["id"], manual)
    )
    summary = " ".join(after_assignment.text.split())
    assert "<dt>Automatisch verbucht</dt> <dd>1</dd>" in summary
    assert "<dt>Manuell zugeordnet</dt> <dd>1</dd>" in summary
    assert "<dt>Warten auf Zuordnung</dt> <dd>0</dd>" in summary


def test_payments_lists_only_delivered_unpaid_invoices_with_due_state(workspace):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from tests.test_store_billing_postgres import _insert_issued_invoice

    today = datetime.now(ZoneInfo("Europe/Zurich")).date()
    overdue = _invoice("OVERDUE-INVOICE")
    due = _invoice("DUE-TODAY-INVOICE")
    unknown = _invoice("NO-DUE-DATE-INVOICE")
    unreadable = _invoice("UNREADABLE-TOTAL-INVOICE")
    paid = _invoice("PAID-INVOICE")
    cancelled = _invoice("CANCELLED-INVOICE")
    foreign = _invoice("FOREIGN-INVOICE")
    _insert_issued_invoice("ISSUED-ONLY-INVOICE")
    db.record_invoice_payment(paid, COMMUNITY, ACTOR, today, "manual")
    db.cancel_invoice(cancelled, COMMUNITY, ACTOR, "cancelled")
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("ALTER TABLE invoices DISABLE TRIGGER USER")
        cur.execute(
            "UPDATE invoices SET due_date=%s, gross_chf=1234.50 WHERE id=%s",
            (today - timedelta(days=1), overdue),
        )
        cur.execute("UPDATE invoices SET due_date=%s WHERE id=%s", (today, due))
        cur.execute(
            "UPDATE invoices SET due_date=NULL, gross_chf=NULL WHERE id=%s", (unknown,)
        )
        cur.execute("UPDATE invoices SET gross_chf='NaN' WHERE id=%s", (unreadable,))
        cur.execute(
            "UPDATE invoices SET community_id='foreign' WHERE id=%s", (foreign,)
        )
        cur.execute("ALTER TABLE invoices ENABLE TRIGGER USER")
        cur.execute("ALTER TABLE invoice_lifecycle_events DISABLE TRIGGER USER")
        cur.execute(
            "UPDATE invoice_lifecycle_events SET community_id='foreign' WHERE invoice_id=%s",
            (foreign,),
        )
        cur.execute("ALTER TABLE invoice_lifecycle_events ENABLE TRIGGER USER")

    response = workspace.get(PAYMENTS)
    assert response.status_code == 200
    assert "Keine offenen Rechnungen." not in response.text
    open_section = response.text.split('id="open-invoices"', 1)[1]

    def invoice_card(invoice):
        return unescape(
            open_section.split(f'id="open-invoice-{invoice}"', 1)[1].split("</li>", 1)[
                0
            ]
        )

    for number in ("PAID", "CANCELLED", "FOREIGN", "ISSUED-ONLY"):
        assert f"{number}-INVOICE" not in open_section

    overdue_card = invoice_card(overdue)
    assert "CHF 1'234.50" in overdue_card
    assert "Überfällig" in overdue_card
    assert f'datetime="{(today - timedelta(days=1)).isoformat()}"' in overdue_card
    assert "Überfällig" not in invoice_card(due)
    assert "Fällig heute" in invoice_card(due)
    assert "Nicht angegeben" in invoice_card(unknown)
    assert "Gesamtbetrag unlesbar" in invoice_card(unknown)
    assert "Gesamtbetrag unlesbar" in invoice_card(unreadable)

    invoice_url = f"{PAYMENTS.removesuffix('/payments')}/invoices/{overdue}"
    assert f'href="{invoice_url}#record-payment"' in overdue_card
    assert "Zahlung erfassen" in overdue_card
    detail = workspace.get(f"{invoice_url}#record-payment")
    assert f'action="{BILLING}/invoice/{overdue}/paid"' in detail.text
    assert 'id="record-payment"' in detail.text

    _set_session(workspace, AUDITOR)
    readonly = workspace.get(PAYMENTS)
    assert "OVERDUE-INVOICE" in readonly.text
    assert "#record-payment" not in readonly.text


def test_payment_invoice_pagination_keeps_older_assignment_targets(
    workspace, monkeypatch
):
    import dashboard

    monkeypatch.setattr(dashboard, "_BILLING_PAGE_SIZE", 2)
    older = _invoice("OLDER-OPEN")
    _invoice("NEWER-OPEN")
    newer = _invoice("NEWEST-OPEN")
    entry, _ = _import()
    first_page = workspace.get(PAYMENTS)
    assert "OLDER-OPEN" not in first_page.text
    assert f'<option value="{newer}">NEWEST-OPEN' in first_page.text

    older_url = f"{PAYMENTS}?invoice_before={newer - 1}#payments"
    assert f'href="{older_url}"' in first_page.text
    older_page = workspace.get(older_url)
    assert f'<option value="{older}">OLDER-OPEN' in older_page.text
    assert f'id="open-invoice-{older}"' in older_page.text
    assert "NEWEST-OPEN" not in older_page.text
    assert (
        _assignment_outcome(workspace, _assign(workspace, entry, older)) == "assigned"
    )


def test_payment_summary_cannot_read_another_legs_import(workspace):
    _upload_xml(workspace, _statement_xml(reference="LOCAL-ENTRY"))
    _upload_xml(
        workspace,
        _statement_xml(
            statement="other", reference="FOREIGN-ENTRY", servicer="foreign"
        ),
    )
    foreign = db.list_bank_statement_entries(COMMUNITY)[0]
    import_id = foreign["statement_import_id"]
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE bank_statement_imports SET community_id='foreign', source_name='foreign.xml' WHERE id=%s",
            (import_id,),
        )
        cur.execute(
            "UPDATE bank_statement_entries SET community_id='foreign' WHERE statement_import_id=%s",
            (import_id,),
        )

    latest = workspace.get(PAYMENTS)
    assert latest.status_code == 200
    assert "LOCAL-ENTRY" in latest.text
    assert "fictional.xml" in latest.text
    assert "FOREIGN-ENTRY" not in latest.text
    assert "foreign.xml" not in latest.text
    assert "Dieser Kontoauszug wurde bereits importiert." not in latest.text


@pytest.mark.parametrize(
    ("actor", "csrf", "amount", "expected"),
    [
        (ADMIN, "", b'<Amt Ccy="CHF">9.67</Amt>', 400),
        (ACTOR, "csrf-secret", b'<Amt Ccy="CHF">9.67</Amt>', 403),
        (ADMIN, "csrf-secret", b'<Amt Ccy="CHF">NaN</Amt>', 400),
        (ADMIN, "csrf-secret", b"", 400),
    ],
    ids=["csrf", "preparer", "unreadable-amount", "missing-amount"],
)
def test_statement_upload_refusals_leave_the_payments_page_empty(
    workspace, actor, csrf, amount, expected
):
    from io import BytesIO

    _set_session(workspace, actor)
    statement = _statement_xml().replace(b'<Amt Ccy="CHF">9.67</Amt>', amount)
    response = workspace.post(
        f"{BILLING}/statements",
        data={"csrf_token": csrf, "statement": (BytesIO(statement), "refused.xml")},
    )
    assert response.status_code == expected
    page = workspace.get(PAYMENTS)
    assert "Noch kein Kontoauszug importiert." in page.text
    assert "Keine Einträge warten auf eine Zuordnung." in page.text
    assert db.list_bank_statement_entries(COMMUNITY) == []


@pytest.mark.parametrize(
    ("locale", "title", "empty"),
    [
        ("fr-CH", "Paiements", "Aucun relevé bancaire importé pour le moment."),
        ("en", "Payments", "No bank statement has been imported yet."),
        ("it-CH", "Pagamenti", "Non è ancora stato importato alcun estratto conto."),
    ],
)
def test_payment_title_and_empty_state_are_localized(
    workspace, app_module, monkeypatch, locale, title, empty
):
    monkeypatch.setitem(
        app_module.web.config, "SITE_RELEASE_ENABLED", locale != "fr-CH"
    )
    response = workspace.get(
        PAYMENTS, query_string={"lang": locale}, follow_redirects=True
    )
    assert response.status_code == 200
    assert f"<title>{title} | OpenLEG</title>" in response.text
    assert empty in response.text


@pytest.mark.parametrize(
    ("facts", "refusal"),
    [
        ({"currency": "EUR"}, "Die Währung ist nicht CHF."),
        ({"direction": "DBIT"}, "Das ist eine Belastung, keine Gutschrift."),
        ({"booking_status": "PDNG"}, "Der Auszug weist keine definitive Buchung aus."),
        (
            {"amount_basis": None},
            "Der Auszug belegt den Betrag für diesen Eintrag nicht eindeutig.",
        ),
        ({"reference": "\u2003\t"}, "Die Zahlung hat keine Referenz."),
        ({"is_reversal": True}, "Die Bank hat die Zahlung zurückgebucht."),
    ],
    ids=["currency", "debit", "pending", "amount-basis", "reference", "reversal"],
)
def test_payment_summary_excludes_entry_level_refusals(workspace, facts, refusal):
    _invoice("AVAILABLE-TARGET")
    entry, _ = _import(**facts)
    page = workspace.get(PAYMENTS)
    assert page.status_code == 200
    assert refusal in page.text
    assert f"/statements/{entry}/assign" not in page.text
    assert "<dt>Warten auf Zuordnung</dt> <dd>0</dd>" in " ".join(page.text.split())


def _upload_statement(client, body):
    from io import BytesIO

    response = client.post(
        f"{BILLING}/statements",
        data={"csrf_token": "csrf-secret", "statement": (BytesIO(body), "upload.xml")},
    )
    assert response.status_code == 302
    return response.location


def _assert_upload_result(page, *, imported=False, duplicate=False):
    assert page.status_code == 200
    assert (
        "Der Kontoauszug wurde importiert und abgeglichen." in page.text
    ) is imported
    assert ("Dieser Kontoauszug wurde bereits importiert." in page.text) is duplicate
    skipped = 1 if duplicate else 0
    assert f"<dt>Doppelte Einträge übersprungen</dt> <dd>{skipped}</dd>" in " ".join(
        page.text.split()
    )


def test_overlapping_upload_redirects_survive_cookie_response_order(
    workspace, app_module
):
    _set_session(workspace, ADMIN)
    cookie_name = app_module.web.config["SESSION_COOKIE_NAME"]
    second_tab = app_module.web.test_client()
    second_tab.set_cookie(cookie_name, workspace.get_cookie(cookie_name).value)

    # Both requests carry the browser's original cookie. The first upload's
    # response arrives last, so its cookie state is what the browser retains.
    body = _statement_xml(reference="unknown")
    first = _upload_statement(workspace, body)
    duplicate = _upload_statement(second_tab, body)
    _assert_upload_result(workspace.get(duplicate), duplicate=True)
    _assert_upload_result(workspace.get(first), imported=True)
    assert len(db.list_bank_statement_entries(COMMUNITY)) == 1

    later = [_upload_statement(workspace, body) for _ in range(6)]
    for location in (duplicate, *later):
        _assert_upload_result(workspace.get(location), duplicate=True)
    _assert_upload_result(workspace.get(first), imported=True)


def test_upload_outcomes_are_scoped_and_survive_reload(workspace):
    _set_session(workspace, ADMIN)
    body = _statement_xml(reference="unknown")
    first = _upload_statement(workspace, body)
    duplicate = _upload_statement(workspace, body)
    assert first != duplicate
    for location in (first, duplicate):
        assert urlsplit(location).path == PAYMENTS
        assert parse_qs(urlsplit(location).query).keys() == {"upload"}

    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities (community_id, name) VALUES ('other-leg', 'Other LEG')"
        )
        cur.execute(
            """INSERT INTO community_members (community_id, building_id, role, status, access_roles)
               VALUES ('other-leg', %s, 'admin', 'confirmed', %s)""",
            (ADMIN, Json(["admin"])),
        )
    for _ in range(2):
        _assert_upload_result(
            workspace.get(first + "&statement=duplicate"), imported=True
        )
        _assert_upload_result(
            workspace.get(duplicate + "&statement=imported"), duplicate=True
        )
    _assert_upload_result(workspace.get(f"{PAYMENTS}?statement=duplicate"))

    unknown = f"{PAYMENTS}?upload={'0' * 32}&statement=duplicate"
    wrong_leg = duplicate.replace(f"/{COMMUNITY}/", "/other-leg/")
    for location in (unknown, wrong_leg, f"{PAYMENTS}?upload=invalid"):
        page = workspace.get(location)
        assert page.status_code == 404
        assert "Dieser Kontoauszug wurde bereits importiert." not in page.text
        assert "upload.xml" not in page.text

    _set_session(workspace, AUDITOR)
    for location in (first, duplicate):
        page = workspace.get(location)
        assert page.status_code == 404
        assert "upload.xml" not in page.text
    assert workspace.get(PAYMENTS).status_code == 200


def _upload_exists(location):
    upload_id = parse_qs(urlsplit(location).query)["upload"][0]
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM bank_statement_uploads WHERE id=%s", (upload_id,))
        return cur.fetchone() is not None


@pytest.mark.parametrize("delete", ["membership", "profile", "leg"])
def test_upload_outcome_is_deleted_with_its_owner(workspace, delete):
    _set_session(workspace, ADMIN)
    location = _upload_statement(workspace, _statement_xml(reference="unknown"))
    _assert_upload_result(workspace.get(location), imported=True)
    assert _upload_exists(location)
    if delete == "profile":
        assert db.delete_building(ADMIN)
    else:
        with db.get_connection() as conn, conn.cursor() as cur:
            if delete == "membership":
                cur.execute(
                    "DELETE FROM community_members WHERE community_id=%s AND building_id=%s",
                    (COMMUNITY, ADMIN),
                )
            else:
                cur.execute(
                    "DELETE FROM communities WHERE community_id=%s", (COMMUNITY,)
                )
    assert not _upload_exists(location)
    assert len(db.list_bank_statement_entries(COMMUNITY)) == 1


def test_recreated_profile_and_membership_cannot_read_old_upload(workspace):
    _set_session(workspace, ADMIN)
    body = _statement_xml(reference="unknown")
    location = _upload_statement(workspace, body)
    assert db.delete_building(ADMIN)
    assert db.save_building(
        ADMIN,
        ADMIN + "@example.invalid",
        {"address": "Recreated profile", "lat": 47.2, "lon": 8.2},
        {},
        verified=True,
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO community_members (community_id, building_id, role, status)
               VALUES (%s, %s, 'admin', 'confirmed')""",
            (COMMUNITY, ADMIN),
        )
    _set_session(workspace, ADMIN)
    assert workspace.get(location).status_code == 404
    _assert_upload_result(
        workspace.get(_upload_statement(workspace, body)), duplicate=True
    )


def test_schema_adds_upload_outcomes_to_an_existing_database(workspace):
    _, result = _import()
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE bank_statement_uploads")
    db.create_tables()
    _set_session(workspace, ADMIN)
    location = _upload_statement(workspace, _statement_xml(reference="unknown"))
    db.create_tables()
    _assert_upload_result(workspace.get(location), imported=True)
    assert any(
        row["statement_import_id"] == result["statement_import_id"]
        for row in db.list_bank_statement_entries(COMMUNITY)
    )


def test_upload_holds_a_membership_lock_that_blocks_status_updates(
    workspace, monkeypatch
):
    from psycopg2.errors import LockNotAvailable

    from store import billing

    import_result = billing._statement_import_result

    def check_lock(*args, **kwargs):
        # Probe the lock an UPDATE of a non-key column such as status needs.
        with (
            pytest.raises(LockNotAvailable),
            db.get_connection() as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                """
                SELECT id FROM community_members
                WHERE community_id=%s AND building_id=%s
                FOR NO KEY UPDATE NOWAIT
                """,
                (COMMUNITY, ADMIN),
            )
        return import_result(*args, **kwargs)

    monkeypatch.setattr(billing, "_statement_import_result", check_lock)
    _set_session(workspace, ADMIN)
    location = _upload_statement(workspace, _statement_xml(reference="unknown"))
    _assert_upload_result(workspace.get(location), imported=True)


def test_unknown_upload_does_not_consume_assignment_feedback_after_role_revocation(
    workspace,
):
    invoice = _invoice()
    entry, _ = _import()
    response = _assign(workspace, entry, invoice, confirm="")
    assert response.status_code == 303
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE community_members SET access_roles='[]' WHERE community_id=%s AND building_id=%s",
            (COMMUNITY, ACTOR),
        )

    redirect = urlsplit(response.location)
    unknown = redirect._replace(query=f"{redirect.query}&upload={'0' * 32}").geturl()
    rejected = workspace.get(unknown)
    assert rejected.status_code == 403
    assert 'id="assignment-feedback"' not in rejected.text

    denial = workspace.get(response.location)
    assert denial.status_code == 200
    assert "Sie dürfen in dieser LEG keine Zahlungen zuordnen." in denial.text
    assert workspace.get(response.location).status_code == 403
