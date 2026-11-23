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
        ("missing_reference", "Eine Zahlungsreferenz ist erforderlich."),
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
    assert 'id="assignment-feedback" role="alert"' in response.text
    assert message in response.text
    if case == "missing_reference":
        assert "Die Zahlung hat keine Referenz." in response.text
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
    details=b"",
    reversal=b"",
):
    from tests.test_payment_reconciliation import CAMT_053

    account_xml = (
        f"<Acct><Id><Othr><Id>{account}</Id></Othr></Id></Acct>" if account else ""
    )
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


@pytest.mark.parametrize("second_match", ["reviewed", "automatic"])
def test_same_bank_credit_cannot_pay_another_invoice(workspace, second_match):
    first = _invoice("FIRST-INVOICE")
    second = _invoice("SECOND-INVOICE")
    _upload_xml(
        workspace,
        _statement_xml(reference="FIRST-INVOICE"),
        _statement_xml(
            statement="statement-2",
            reference="FIRST-INVOICE"
            if second_match == "reviewed"
            else "SECOND-INVOICE",
        ),
    )
    duplicate, original = db.list_bank_statement_entries(COMMUNITY)
    assert original["invoice_id"] == first
    if second_match == "reviewed":
        response = _assign(workspace, duplicate["id"], second)
        response = _follow_assignment(workspace, response)
        assert "bereits auf Rechnung FIRST-INVOICE gebucht" in response.text
    assert not any(
        event["event_type"] == "paid"
        for event in db.list_invoice_events(second, COMMUNITY)
    )
    assert (
        len(db.list_community_operator_events(COMMUNITY, event_types=("invoice.paid",)))
        == 1
    )


@pytest.mark.parametrize("booking", ["import", "assignment"])
def test_rejected_duplicate_supplies_trusted_aliases(workspace, booking):
    first = _invoice("FIRST-INVOICE")
    second = _invoice("SECOND-INVOICE")
    original_ref = "FIRST-INVOICE" if booking == "import" else "unknown"
    original = _with_entry_servicer(_statement_xml(reference=original_ref))
    alternate = _with_entry_servicer(_statement_xml(statement="statement-2")).replace(
        b"shared-entry-reference", b"alternate-entry-reference"
    )
    _upload_xml(workspace, original, alternate)
    alias, booked = db.list_bank_statement_entries(COMMUNITY)
    if booking == "assignment":
        assert (
            _assignment_outcome(workspace, _assign(workspace, booked["id"], first))
            == "assigned"
        )
        assert (
            _assignment_outcome(workspace, _assign(workspace, alias["id"], second))
            == "refused"
        )
    else:
        assert booked["invoice_id"] == first
        assert alias["duplicate_reason"] == "duplicate_payment"
    _upload_xml(
        workspace,
        _with_entry_servicer(
            _statement_xml(
                statement="statement-3", reference="SECOND-INVOICE", servicer=""
            )
        ).replace(b"shared-entry-reference", b"alternate-entry-reference"),
    )
    newest = db.list_bank_statement_entries(COMMUNITY)[0]
    assert newest["duplicate_reason"] == "duplicate_payment"
    refused = _follow_assignment(workspace, _assign(workspace, newest["id"], second))
    assert (
        "Diese Zahlung ist bereits auf Rechnung FIRST-INVOICE gebucht." in refused.text
    )
    assert len(_paid_events()) == 1


def test_reformatted_statement_without_servicer_ref_is_held_for_review(workspace):
    first = _invoice("FIRST-INVOICE")
    second = _invoice("SECOND-INVOICE")
    statement = _statement_xml(reference="FIRST-INVOICE", servicer="")
    reformatted = statement.replace(b"<Ntry>", b"<Ntry>\n  ")
    _upload_xml(workspace, statement, reformatted)
    repeated, booked = db.list_bank_statement_entries(COMMUNITY)
    assert repeated["statement_import_id"] != booked["statement_import_id"]
    assert repeated["entry_reference"] == booked["entry_reference"]
    assert booked["invoice_id"] == first
    assert repeated["duplicate_reason"] == "possible_duplicate_payment"
    refusal = _follow_assignment(workspace, _assign(workspace, repeated["id"], second))
    _review_token(refusal)
    assert len(_paid_events()) == 1


def test_alias_from_a_possible_duplicate_still_allows_review(workspace):
    _invoice("FIRST-INVOICE")
    second = _invoice("SECOND-INVOICE")
    _upload_xml(
        workspace,
        _batch_with_transaction_reference(),
        _with_entry_servicer(
            _statement_xml(statement="statement-2", servicer="alternate-transaction")
        ),
        _statement_xml(
            statement="statement-3",
            servicer="alternate-transaction",
            reference="SECOND-INVOICE",
        ),
    )
    candidate = db.list_bank_statement_entries(COMMUNITY)[0]
    assert candidate["duplicate_reason"] == "possible_duplicate_payment"
    refused = _follow_assignment(workspace, _assign(workspace, candidate["id"], second))
    token = _review_token(refused)
    accepted = _assign(
        workspace,
        candidate["id"],
        second,
        duplicate_review=token,
        confirm_distinct="yes",
    )
    assert _assignment_outcome(workspace, accepted) == "assigned"
    assert _entry_booking(candidate["id"]) == ("matched", second)
    assert len(_paid_events()) == 2


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


@pytest.mark.parametrize(
    "case", ["different-account", "generated-reference", "missing-account"]
)
def test_bank_credit_identity_uses_account_and_servicer_reference(workspace, case):
    first = _invoice("FIRST-INVOICE")
    second = _invoice("SECOND-INVOICE")
    _upload_xml(
        workspace,
        _statement_xml(
            reference="FIRST-INVOICE",
            servicer="" if case == "generated-reference" else "bank-entry-1",
            reversal=b"<RvslInd>0</RvslInd>",
        ),
        _statement_xml(
            statement="statement-1" if case == "generated-reference" else "statement-2",
            reference="unknown",
            account={
                "missing-account": "",
                "different-account": "another-account",
                "generated-reference": "fictional-account",
            }[case],
            servicer="" if case == "generated-reference" else "bank-entry-1",
            reversal=b"<RvslInd>false</RvslInd>",
        ),
    )
    newest, original = db.list_bank_statement_entries(COMMUNITY)
    assert original["invoice_id"] == first
    if case == "generated-reference":
        assert newest["entry_reference"] == original["entry_reference"]
    response = _assign(workspace, newest["id"], second)
    assert _assignment_outcome(workspace, response) == (
        "refused" if case == "missing-account" else "assigned"
    )
    if case == "generated-reference":
        assert _entry_booking(newest["id"]) == ("matched", second)


def _entry_booking(entry_id):
    entry = next(
        row
        for row in db.list_bank_statement_entries(COMMUNITY)
        if row["id"] == entry_id
    )
    return entry["match_decision"], entry["invoice_id"]


def _concurrent_assignments(app_module, monkeypatch, entry_ids, invoices):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic

    from store import billing

    checked_first, checked_second, release_first = Event(), Event(), Event()
    check_duplicate = billing._bank_payment_duplicate

    def pause_after_check(cur, community_id, entry, import_id):
        result = check_duplicate(cur, community_id, entry, import_id)
        if entry["id"] == entry_ids[0]:
            checked_first.set()
            assert release_first.wait(10)
        else:
            checked_second.set()
        return result

    def assign(entry, invoice):
        with app_module.web.test_client() as client:
            _set_session(client, ACTOR)
            return _assignment_outcome(client, _assign(client, entry, invoice))

    monkeypatch.setattr(billing, "_bank_payment_duplicate", pause_after_check)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(assign, entry_ids[0], invoices[0])
        try:
            assert checked_first.wait(10)
            second = pool.submit(assign, entry_ids[1], invoices[1])
            deadline = monotonic() + 5
            # Release the first writer once the second either waits on the
            # LEG lock or has read the old snapshot with the lock removed.
            with db.get_connection() as conn, conn.cursor() as cur:
                while not checked_second.wait(0.01):
                    cur.execute(
                        """SELECT EXISTS (
                               SELECT 1 FROM pg_locks WHERE locktype = 'advisory'
                               AND database = (SELECT oid FROM pg_database
                                               WHERE datname = current_database())
                               AND NOT granted
                           ) AS waiting"""
                    )
                    if cur.fetchone()["waiting"]:
                        break
                    assert monotonic() < deadline
        finally:
            release_first.set()
        return [first.result(), second.result()]


def test_concurrent_review_of_repeated_bank_credit_only_books_once(
    workspace, app_module, monkeypatch
):
    invoices = [_invoice("FIRST-INVOICE"), _invoice("SECOND-INVOICE")]
    _upload_xml(workspace, _statement_xml(), _statement_xml(statement="statement-2"))
    entries = db.list_bank_statement_entries(COMMUNITY)
    outcomes = _concurrent_assignments(
        app_module, monkeypatch, [entry["id"] for entry in entries], invoices
    )
    assert sorted(outcomes) == ["assigned", "refused"]
    assert (
        len(
            db.list_community_operator_events(
                COMMUNITY, event_types=("invoice.paid", "payment.match.confirmed")
            )
        )
        == 2
    )


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


def _as_legacy(entry):
    """Leave exactly the bank provenance available before the upgrade."""
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE bank_statement_entries
               SET bank_servicer_key=NULL, bank_account_key=''
               WHERE id=%s""",
            (entry["id"],),
        )
        if "bank_reference_keys" in entry:
            cur.execute(
                "UPDATE bank_statement_entries SET bank_reference_keys=NULL WHERE id=%s",
                (entry["id"],),
            )
        if "bank_typed_reference_keys" in entry:
            cur.execute(
                "UPDATE bank_statement_entries SET bank_typed_reference_keys=NULL WHERE id=%s",
                (entry["id"],),
            )


def _with_entry_servicer(statement):
    return statement.replace(
        b"</BookgDt>",
        b"</BookgDt><AcctSvcrRef>shared-entry-reference</AcctSvcrRef>",
    )


@pytest.mark.parametrize("case", ("legacy", "overlap"))
@pytest.mark.parametrize("second_match", ("reviewed", "automatic"))
def test_export_duplicate_cannot_book_twice(workspace, case, second_match):
    first = _invoice("FIRST-INVOICE")
    second = _invoice("SECOND-INVOICE")
    original_xml = _statement_xml(reference="FIRST-INVOICE")
    repeated_xml = _statement_xml(
        statement="statement-2",
        reference="SECOND-INVOICE" if second_match == "automatic" else "unknown",
        servicer="" if case == "overlap" else "bank-entry-1",
    )
    if case == "overlap":
        original_xml = _with_entry_servicer(original_xml)
        repeated_xml = _with_entry_servicer(repeated_xml)
    _upload_xml(workspace, original_xml)
    (original,) = db.list_bank_statement_entries(COMMUNITY)
    assert original["invoice_id"] == first
    if case == "legacy":
        (audit,) = [
            event
            for event in db.list_community_invoice_events(COMMUNITY)
            if event["event_type"] == "paid"
        ]
        assert audit["payment_match"] is None
        _as_legacy(original)
    _upload_xml(workspace, repeated_xml)
    duplicate = db.list_bank_statement_entries(COMMUNITY)[0]
    if second_match == "reviewed":
        refusal = _assign(workspace, duplicate["id"], second)
        refusal = _follow_assignment(workspace, refusal)
        if case == "legacy":
            assert (
                "Diese Zahlung könnte bereits auf Rechnung FIRST-INVOICE verbucht sein."
                in refusal.text
            )
        else:
            assert "bereits auf Rechnung FIRST-INVOICE gebucht" in refusal.text
    else:
        assert duplicate["match_decision"] == "mismatch"
    assert not any(
        event["event_type"] == "paid"
        for event in db.list_invoice_events(second, COMMUNITY)
    )
    assert (
        len(db.list_community_operator_events(COMMUNITY, event_types=("invoice.paid",)))
        == 1
    )


@pytest.mark.parametrize("case", ("legacy", "overlap"))
def test_concurrent_export_duplicates_only_book_once(
    workspace, app_module, monkeypatch, case
):
    invoices = [_invoice("FIRST-INVOICE"), _invoice("SECOND-INVOICE")]
    first_xml = _statement_xml()
    second_xml = _statement_xml(
        statement="statement-2", servicer="" if case == "overlap" else "bank-entry-1"
    )
    if case == "overlap":
        first_xml = _with_entry_servicer(first_xml)
        second_xml = _with_entry_servicer(second_xml)
    _upload_xml(workspace, first_xml, second_xml)
    newest, oldest = db.list_bank_statement_entries(COMMUNITY)
    if case == "legacy":
        _as_legacy(oldest)
    outcomes = _concurrent_assignments(
        app_module, monkeypatch, [oldest["id"], newest["id"]], invoices
    )
    assert sorted(outcomes) == ["assigned", "refused"]
    assert (
        len(db.list_community_operator_events(COMMUNITY, event_types=("invoice.paid",)))
        == 1
    )


@pytest.mark.parametrize("variant", ("different-date", "generated-reference"))
def test_legacy_comparison_distinguishes_unrelated_credits(workspace, variant):
    first = _invoice("FIRST-INVOICE")
    second = _invoice("SECOND-INVOICE")
    generated = variant == "generated-reference"
    servicer = "" if generated else "bank-entry-1"
    _upload_xml(workspace, _statement_xml(reference="FIRST-INVOICE", servicer=servicer))
    (original,) = db.list_bank_statement_entries(COMMUNITY)
    assert original["invoice_id"] == first
    _as_legacy(original)
    new_xml = _statement_xml(reference="unknown", servicer=servicer)
    if not generated:
        new_xml = new_xml.replace(b"2026-09-14", b"2026-09-15")
    _upload_xml(workspace, new_xml)
    duplicate = db.list_bank_statement_entries(COMMUNITY)[0]
    assert duplicate["entry_reference"] == original["entry_reference"]
    response = _assign(workspace, duplicate["id"], second)
    assert _assignment_outcome(workspace, response) == "assigned"
    assert _entry_booking(duplicate["id"]) == ("matched", second)


def _review_token(page):
    import re

    assert "Trotzdem zuordnen" in page.text
    assert (
        "Ich habe beide Kontoauszüge geprüft. Es ist eine andere Zahlung." in page.text
    )
    return re.search(r'name="duplicate_review" value="([^"]+)"', page.text).group(1)


def _paid_events():
    return db.list_community_operator_events(COMMUNITY, event_types=("invoice.paid",))


def test_positional_legacy_export_holds_repeated_credit(workspace):
    first = _invoice("FIRST-INVOICE")
    second = _invoice("SECOND-INVOICE")
    original_xml = _with_entry_servicer(
        _statement_xml(reference="FIRST-INVOICE", servicer="")
    )
    _upload_xml(workspace, original_xml)
    (original,) = db.list_bank_statement_entries(COMMUNITY)
    assert original["entry_reference"] == "statement-1:1:1"
    assert original["invoice_id"] == first
    _as_legacy(original)
    _upload_xml(workspace, original_xml.replace(b"statement-1", b"statement-2"))
    repeat = db.list_bank_statement_entries(COMMUNITY)[0]
    assert repeat["entry_reference"] == "statement-2:1:1"
    assert (
        _assignment_outcome(workspace, _assign(workspace, repeat["id"], second))
        == "refused"
    )
    assert len(_paid_events()) == 1
    assert repeat["duplicate_reason"] == "possible_duplicate_payment"


def _batch_with_transaction_reference():
    statement = _statement_xml(
        reference="FIRST-INVOICE", servicer="shared-entry-reference"
    )
    start = statement.index(b"<TxDtls>")
    end = statement.index(b"</TxDtls>") + len(b"</TxDtls>")
    details = statement[start:end].replace(
        b"<TxDtls>", b'<TxDtls><Amt Ccy="CHF">9.67</Amt>'
    )
    other = details.replace(b"FIRST-INVOICE", b"unallocated").replace(
        b"shared-entry-reference", b"separate-transaction"
    )
    return (statement[:start] + details + other + statement[end:]).replace(
        b'<Ntry>\n    <Amt Ccy="CHF">9.67', b'<Ntry>\n    <Amt Ccy="CHF">19.34', 1
    )


@pytest.mark.parametrize(
    "case", ["legacy-other-account", "cross-level", "unknown-account"]
)
def test_reviewed_possible_duplicate_books_with_source_audit(workspace, case):
    _invoice("FIRST-INVOICE")
    second = _invoice("SECOND-INVOICE")
    if case != "cross-level":
        _upload_xml(
            workspace,
            _statement_xml(
                reference="FIRST-INVOICE",
                account="" if case == "unknown-account" else "fictional-account",
            ),
        )
        (first,) = db.list_bank_statement_entries(COMMUNITY)
        if case == "legacy-other-account":
            _as_legacy(first)
        candidate = _statement_xml(
            statement="statement-2",
            account="another-account",
            reference="SECOND-INVOICE",
        )
    else:
        _upload_xml(workspace, _batch_with_transaction_reference())
        first = next(
            row
            for row in db.list_bank_statement_entries(COMMUNITY)
            if row["match_decision"] == "matched"
        )
        candidate = _with_entry_servicer(
            _statement_xml(
                statement="statement-2",
                reference="SECOND-INVOICE",
                servicer="new-transaction",
            )
        )
    _upload_xml(workspace, candidate)
    repeat = db.list_bank_statement_entries(COMMUNITY)[0]
    assert repeat["match_decision"] == "mismatch"
    refusal = _follow_assignment(workspace, _assign(workspace, repeat["id"], second))
    assert (
        "Diese Zahlung könnte bereits auf Rechnung FIRST-INVOICE verbucht sein."
        in refusal.text
    )
    token = _review_token(refusal)
    assert repeat["duplicate_reason"] == "possible_duplicate_payment"
    before = len(_paid_events())
    assert (
        _assignment_outcome(
            workspace, _assign(workspace, repeat["id"], second, duplicate_review=token)
        )
        == "refused"
    )
    assert len(_paid_events()) == before
    if case == "legacy-other-account":
        other = _invoice("OTHER-TARGET")
        for target, fields in [
            (other, {"confirm_distinct": "yes"}),
            (second, {"confirm_distinct": "yes", "csrf_token": "wrong"}),
        ]:
            assert (
                _assignment_outcome(
                    workspace,
                    _assign(
                        workspace,
                        repeat["id"],
                        target,
                        duplicate_review=token,
                        **fields,
                    ),
                )
                == "refused"
            )
        _set_session(workspace, AUDITOR)
        assert (
            _assignment_outcome(
                workspace,
                _assign(
                    workspace,
                    repeat["id"],
                    second,
                    duplicate_review=token,
                    confirm_distinct="yes",
                ),
            )
            == "refused"
        )
        _set_session(workspace, ACTOR)
        assert len(_paid_events()) == before
    assert (
        _assignment_outcome(
            workspace,
            _assign(
                workspace,
                repeat["id"],
                second,
                duplicate_review=token,
                confirm_distinct="yes",
            ),
        )
        == "assigned"
    )
    assert len(_paid_events()) == before + 1
    (paid,) = [
        event
        for event in db.list_community_invoice_events(COMMUNITY)
        if event["event_type"] == "paid" and event["invoice_id"] == second
    ]
    assert paid["payment_match"] == {
        "entry_id": repeat["id"],
        "decision": "matched",
        "duplicate_override": {
            "actor_id": ACTOR,
            "earlier_entry_id": first["id"],
            "decision": "possible duplicate confirmed",
        },
    }
    assert _entry_booking(repeat["id"]) == ("matched", second)

    if case == "unknown-account":
        assert first["bank_account_key"] == "" and repeat["bank_account_key"]
        assert first["bank_typed_reference_keys"] == repeat["bank_typed_reference_keys"]
        third = _invoice("THIRD-INVOICE")
        _upload_xml(
            workspace,
            _statement_xml(
                statement="statement-3",
                account="another-account",
                reference="THIRD-INVOICE",
            ),
        )
        same_account = db.list_bank_statement_entries(COMMUNITY)[0]
        assert same_account["duplicate_reason"] == "duplicate_payment"
        page = _follow_assignment(
            workspace, _assign(workspace, same_account["id"], third)
        )
        assert (
            "Diese Zahlung ist bereits auf Rechnung SECOND-INVOICE gebucht."
            in page.text
        )
        assert 'name="duplicate_review"' not in page.text
        assert len(_paid_events()) == before + 1


def test_certain_duplicate_refuses_forged_override(workspace):
    _invoice("FIRST-INVOICE")
    second = _invoice("SECOND-INVOICE")
    _upload_xml(
        workspace,
        _statement_xml(reference="FIRST-INVOICE"),
        _statement_xml(statement="statement-2", reference="SECOND-INVOICE"),
    )
    duplicate = db.list_bank_statement_entries(COMMUNITY)[0]
    page = _follow_assignment(
        workspace,
        _assign(
            workspace,
            duplicate["id"],
            second,
            duplicate_review="forged",
            confirm_distinct="yes",
        ),
    )
    assert "Diese Zahlung ist bereits auf Rechnung FIRST-INVOICE gebucht." in page.text
    assert 'name="duplicate_review"' not in page.text
    assert len(_paid_events()) == 1


def test_concurrent_duplicate_overrides_only_add_one_paid_event(workspace, app_module):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    _invoice("FIRST-INVOICE")
    invoices = [_invoice("SECOND-INVOICE"), _invoice("THIRD-INVOICE")]
    _upload_xml(workspace, _statement_xml(reference="FIRST-INVOICE"))
    (previous,) = db.list_bank_statement_entries(COMMUNITY)
    _as_legacy(previous)
    _upload_xml(
        workspace,
        _statement_xml(
            statement="statement-2",
            reference="FIRST-INVOICE",
            servicer="shared-entry-reference",
        ),
        _with_entry_servicer(
            _statement_xml(
                statement="statement-3", reference="FIRST-INVOICE", servicer=""
            )
        ),
    )
    entries = db.list_bank_statement_entries(COMMUNITY)[:2]
    barrier = Barrier(2)

    def submit(entry, invoice):
        with app_module.web.test_client() as client:
            _set_session(client, ACTOR)
            token = _review_token(
                _follow_assignment(client, _assign(client, entry["id"], invoice))
            )
            barrier.wait(timeout=10)
            return _assignment_outcome(
                client,
                _assign(
                    client,
                    entry["id"],
                    invoice,
                    duplicate_review=token,
                    confirm_distinct="yes",
                ),
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, entries, invoices))
    assert sorted(outcomes) == ["assigned", "refused"]
    assert len(_paid_events()) == 2
