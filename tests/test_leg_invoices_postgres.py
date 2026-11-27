# SPDX-License-Identifier: AGPL-3.0-or-later
"""LEG invoice administration through HTTP and PostgreSQL."""

import re
from urllib.parse import urlsplit

import pytest
from psycopg2.extras import Json

import database as db
from tests import test_dashboard_access_routes, test_interest_postgres
from tests.test_dashboard_access_routes import _set_session

app_module = test_dashboard_access_routes.app_module
interest_database = test_interest_postgres.interest_database
pytestmark = pytest.mark.integration

COMMUNITY = "invoice-leg"
OTHER = "other-leg"
ROOT = f"/leg/community/{COMMUNITY}"
LIST = ROOT + "/invoices"
ADMIN = "billing-admin"
PARTICIPANT = "invoice-participant"
AUDITOR = "billing-auditor"


@pytest.fixture
def workspace(interest_database, app_module):
    for actor, address in (
        (ADMIN, "Musterweg 1"),
        (PARTICIPANT, "Bärenweg 12"),
        (AUDITOR, "Musterweg 3"),
    ):
        assert db.save_building(
            actor,
            actor + "@example.invalid",
            {"address": address, "lat": 47.2, "lon": 8.2},
            {},
            verified=True,
        )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO communities (community_id, name) VALUES (%s, %s)",
            [(COMMUNITY, "Invoice LEG"), (OTHER, "Other LEG")],
        )
        cur.executemany(
            """INSERT INTO community_members
               (community_id, building_id, role, status, access_roles)
               VALUES (%s, %s, %s, 'confirmed', %s)""",
            [
                (COMMUNITY, ADMIN, "admin", Json([])),
                (COMMUNITY, PARTICIPANT, "member", Json([])),
                (COMMUNITY, AUDITOR, "member", Json(["auditor"])),
                (OTHER, ADMIN, "admin", Json([])),
            ],
        )
    client = app_module.web.test_client()
    _set_session(client, ADMIN)
    return client


def _invoice(number, *, community=COMMUNITY, participant=PARTICIPANT, amount="12.30"):
    registration = db.get_building(participant)["referral_code"]
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO invoices (
                   community_id, participant_id, invoice_number, status,
                   policy_snapshot, provenance_snapshot, gross_chf, issue_date, due_date
               ) VALUES (%s, %s, %s, 'issued', %s, %s, %s, '2020-02-01', '2020-03-01')
               RETURNING id""",
            (
                community,
                participant,
                number,
                Json(
                    {
                        "delivery_method": "download",
                        "internal_price_chf_per_kwh": "0.15",
                    }
                ),
                Json(
                    {
                        "participant_registration_id": registration,
                        "period_start": "2020-01-01",
                    }
                ),
                amount,
            ),
        )
        return cur.fetchone()["id"]


def _event(invoice, state, *, reference=None, community=COMMUNITY):
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO invoice_lifecycle_events
               (invoice_id,community_id,actor_id,event_type,previous_state,
                new_state,idempotency_key,reference)
               VALUES (%s,%s,%s,%s,'issued',%s,%s,%s)""",
            (invoice, community, ADMIN, state, state, state, reference),
        )


def _question(invoice, *, community=COMMUNITY, status="open"):
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO invoice_queries
               (invoice_id, community_id, participant_id, category, status,
                response_due_at, reminder_due_at)
               VALUES (%s,%s,%s,'amount',%s,now(),now()) RETURNING id""",
            (invoice, community, PARTICIPANT, status),
        )
        return cur.fetchone()["id"]


@pytest.fixture
def invoices(workspace):
    ids = {
        "issued": _invoice("INV-ISSUED", participant=ADMIN),
        "overdue": _invoice("INV-OVERDUE"),
        "paid": _invoice("INV-PAID"),
        "cancelled": _invoice("INV-CANCELLED", amount=None),
        "corrected": _invoice("INV-CORRECTED", amount="NaN"),
        "foreign": _invoice("INV-FOREIGN", community=OTHER),
    }
    _event(ids["overdue"], "delivered")
    _event(ids["paid"], "paid", reference="BANK-123")
    _event(ids["cancelled"], "cancelled")
    _event(ids["corrected"], "corrected")
    _question(ids["paid"], status="resolved")
    _question(ids["foreign"], community=OTHER)
    return ids


def _invoice_links(html):
    return set(re.findall(rf'href="{LIST}/(\d+)"', html))


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("all", {"issued", "overdue", "paid", "cancelled", "corrected"}),
        ("open", {"issued", "overdue"}),
        ("paid", {"paid"}),
        ("cancelled", {"cancelled", "corrected"}),
        ("with-question", {"paid"}),
    ],
)
def test_invoice_filters_are_native_links(workspace, invoices, state, expected):
    response = workspace.get(LIST, query_string={"state": state})
    assert response.status_code == 200
    assert _invoice_links(response.text) == {str(invoices[key]) for key in expected}
    assert f'href="{LIST}?state={state}" aria-current="true"' in response.text


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("  inv-iss  ", {"issued"}),
        ("BÄRENWEG", {"overdue", "paid", "cancelled", "corrected"}),
        ("bank-123", {"paid"}),
        ("INV-FOREIGN", set()),
    ],
)
def test_invoice_search_matches_number_address_or_reference(
    workspace, invoices, query, expected
):
    response = workspace.get(LIST, query_string={"q": query})
    assert response.status_code == 200
    assert _invoice_links(response.text) == {str(invoices[key]) for key in expected}
    assert 'method="get"' in response.text
    assert 'for="invoice-search"' in response.text
    assert 'type="search" id="invoice-search" name="q"' in response.text


def test_invoice_list_combines_filters_and_search(workspace, invoices):
    response = workspace.get(LIST, query_string={"state": "open", "q": "Bärenweg"})
    assert _invoice_links(response.text) == {str(invoices["overdue"])}
    assert f'href="{LIST}?state=paid&amp;q=B%C3%A4renweg"' in response.text
    assert 'type="hidden" name="state" value="open"' in response.text


def test_invoice_list_has_a_title_columns_and_tabular_amounts(workspace, invoices):
    response = workspace.get(LIST)
    assert response.status_code == 200
    assert "<title>Rechnungen | OpenLEG</title>" in response.text
    assert "<table" in response.text
    for text in (
        "Rechnungsnummer",
        "Periode",
        "Teilnehmer",
        "Betrag",
        "Status",
        "Fällig am",
        "Januar 2020",
        "1. März 2020",
        "Bärenweg 12",
        "12.30 CHF",
        "tabular-nums",
    ):
        assert text in response.text
    assert response.text.count("Gesamtbetrag unlesbar") == 2


def test_invoice_list_distinguishes_no_invoices_from_no_matches(workspace):
    assert "Noch keine freigegebenen Rechnungen vorhanden." in workspace.get(LIST).text
    _invoice("INV-1")
    response = workspace.get(LIST, query_string={"q": "no matches"})
    assert "Keine Rechnungen gefunden." in response.text
    assert "Noch keine freigegebenen Rechnungen vorhanden." not in response.text


@pytest.mark.parametrize(
    ("locale", "list_title", "detail_title"),
    [
        ("fr-CH", "Factures", "Facture INV-LOCAL"),
        ("en", "Invoices", "Invoice INV-LOCAL"),
        ("it-CH", "Fatture", "Fattura INV-LOCAL"),
    ],
)
def test_invoice_page_titles_are_localized(
    workspace, app_module, monkeypatch, locale, list_title, detail_title
):
    monkeypatch.setitem(
        app_module.web.config, "SITE_RELEASE_ENABLED", locale != "fr-CH"
    )
    invoice = _invoice("INV-LOCAL")
    listing = workspace.get(LIST, query_string={"lang": locale}, follow_redirects=True)
    assert f"<title>{list_title} | OpenLEG</title>" in listing.text
    assert (
        f"<title>{detail_title} | OpenLEG</title>"
        in workspace.get(f"{LIST}/{invoice}").text
    )


def test_retained_invoice_does_not_show_replacement_registration_address(workspace):
    invoice = _invoice("INV-RETAINED")
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE buildings SET address = 'Neuer Weg 3', referral_code = 'replacement'
               WHERE building_id = %s""",
            (PARTICIPANT,),
        )
    response = workspace.get(LIST, query_string={"q": "Neuer Weg"})
    assert not _invoice_links(response.text)
    detail = workspace.get(f"{LIST}/{invoice}")
    assert detail.status_code == 200
    assert PARTICIPANT in detail.text
    assert "Neuer Weg 3" not in detail.text


def test_invoice_detail_shows_metadata_audit_and_reply(workspace):
    invoice = _invoice("INV-DETAIL")
    _event(invoice, "delivered", reference="Delivery evidence")
    question = _question(invoice)
    assert db.add_invoice_query_message(
        question,
        PARTICIPANT,
        "Bitte prüfen Sie den Betrag.",
        participant_id=PARTICIPANT,
    )
    response = workspace.get(f"{LIST}/{invoice}")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    for text in (
        "<title>Rechnung INV-DETAIL | OpenLEG</title>",
        f'href="{LIST}"',
        "Bärenweg 12",
        "12.30 CHF",
        "Januar 2020",
        "1. März 2020",
        "Audit-Verlauf",
        "Delivery evidence",
        "Bitte prüfen Sie den Betrag.",
        f'action="{ROOT}/billing/queries/{question}"',
        'for="paid-date"',
        'type="date" id="paid-date"',
        'for="payment-reference"',
        'for="cancel-reason"',
        f'for="reply-{question}"',
        f'for="question-status-{question}"',
        'name="csrf_token" value="csrf-secret"',
    ):
        assert text in response.text


@pytest.mark.parametrize("amount", [None, "NaN"])
def test_invoice_detail_flags_unreadable_amount_and_empty_history(workspace, amount):
    invoice = _invoice("INV-AMOUNT", amount=amount)
    response = workspace.get(f"{LIST}/{invoice}")
    assert response.status_code == 200
    assert "Gesamtbetrag unlesbar" in response.text
    assert "Unlesbar CHF" not in response.text
    assert "Noch keine Statusänderung protokolliert." in response.text
    assert "Noch keine Rechnungsfragen vorhanden." in response.text


@pytest.mark.parametrize("actor", [PARTICIPANT, "outsider"])
def test_member_without_billing_access_cannot_open_invoice_pages(workspace, actor):
    invoice = _invoice("INV-PRIVATE")
    _set_session(workspace, actor)
    for path in (LIST, f"{LIST}/{invoice}"):
        response = workspace.get(path)
        assert response.status_code == 403
        assert "INV-PRIVATE" not in response.text


def test_auditor_can_read_invoice_but_cannot_post_actions_or_reply(workspace):
    invoice = _invoice("INV-AUDIT")
    query = _question(invoice)
    _set_session(workspace, AUDITOR)
    response = workspace.get(f"{LIST}/{invoice}")
    assert response.status_code == 200
    assert "INV-AUDIT" in response.text
    assert 'method="post"' not in response.text
    for path, fields in (
        (f"{ROOT}/billing/invoice/{invoice}/cancel", {"reason": "Test"}),
        (f"{ROOT}/billing/queries/{query}", {"message": "Test reply"}),
    ):
        assert (
            workspace.post(
                path, data={"csrf_token": "csrf-secret", **fields}
            ).status_code
            == 403
        )
    assert not db.list_invoice_events(invoice, COMMUNITY)
    assert not db.list_invoice_queries(invoice, community_id=COMMUNITY)[0]["messages"]


def test_foreign_invoice_and_question_are_indistinguishable_from_missing(workspace):
    foreign = _invoice("INV-FOREIGN", community=OTHER)
    question = _question(foreign, community=OTHER)
    for method, path, fields in (
        ("get", f"{LIST}/{{}}", {}),
        ("post", f"{ROOT}/billing/invoice/{{}}/cancel", {"reason": "Test"}),
        ("post", f"{ROOT}/billing/queries/{{}}", {"message": "Test reply"}),
    ):
        foreign_id = question if "/queries/" in path else foreign
        data = {"csrf_token": "csrf-secret", **fields} if method == "post" else None
        response = getattr(workspace, method)(path.format(foreign_id), data=data)
        missing = getattr(workspace, method)(path.format(999), data=data)
        assert response.status_code == missing.status_code == 404
        assert response.data == missing.data
        assert "INV-FOREIGN" not in response.text
        assert response.headers["Cache-Control"] == "no-store"
    assert not db.list_invoice_events(foreign, OTHER)
    assert not db.list_invoice_queries(foreign, community_id=OTHER)[0]["messages"]


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("deliver", "Zugestellt"),
        ("delivery-confirmed", "Zugestellt"),
        ("paid", "Bezahlt"),
        ("cancel", "Storniert"),
        ("correct", "Korrigiert"),
        ("reply", "Die Rechnung stimmt."),
    ],
)
def test_invoice_action_returns_to_affected_detail(workspace, action, expected):
    invoice = _invoice("INV-ACTION")
    fields = {"csrf_token": "csrf-secret"}
    path = f"{ROOT}/billing/invoice/{invoice}/{action}"
    control = {
        "deliver": "Zustellen",
        "delivery-confirmed": "Zustellung als erfolgt bestätigen",
        "paid": "Zahlung erfassen",
        "cancel": "Stornieren",
        "correct": "Korrektur verknüpfen",
        "reply": "Speichern",
    }[action]
    if action == "delivery-confirmed":
        db.prepare_invoice_delivery(invoice, COMMUNITY, ADMIN)
    elif action == "paid":
        db.prepare_invoice_delivery(invoice, COMMUNITY, ADMIN)
        db.complete_invoice_delivery(invoice, COMMUNITY, ADMIN)
        fields.update(paid_date="2020-02-16", reference="TEST-BANK")
    elif action == "cancel":
        fields.update(reason="Falscher Tarif")
    elif action == "correct":
        db.cancel_invoice(invoice, COMMUNITY, ADMIN, "Falscher Tarif")
        fields.update(
            corrected_invoice_id=_invoice("INV-REPLACEMENT"), reason="Korrigiert"
        )
    elif action == "reply":
        question = _question(invoice)
        path = f"{ROOT}/billing/queries/{question}"
        fields.update(message=expected, status="acknowledged")

    detail = workspace.get(f"{LIST}/{invoice}")
    assert detail.status_code == 200
    assert path in re.findall(r'<form method="post" action="([^"]+)"', detail.text)
    assert control in detail.text
    response = workspace.post(path, data=fields)
    assert response.status_code == 302
    assert urlsplit(response.location).path == f"{LIST}/{invoice}"
    assert expected in workspace.get(response.location).text
    assert control not in workspace.get(LIST).text
    assert path not in workspace.get(f"{ROOT}/billing").text


def test_query_reply_needs_csrf(workspace):
    invoice = _invoice("INV-QUERY")
    query = _question(invoice)
    response = workspace.post(
        f"{ROOT}/billing/queries/{query}", data={"message": "Unverified reply"}
    )
    assert response.status_code == 400
    assert not db.list_invoice_queries(invoice, community_id=COMMUNITY)[0]["messages"]


def test_invoice_pages_bound_rows_and_keep_search_and_state(workspace):
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO invoices (community_id, invoice_number, status, issue_date)
               SELECT %s, 'BULK-' || n, 'issued', '2020-02-01'
               FROM generate_series(1, 101) AS n RETURNING id""",
            (COMMUNITY,),
        )
        ids = sorted((row["id"] for row in cur.fetchall()), reverse=True)

    for page, expected in enumerate((ids[:50], ids[50:100], ids[100:]), start=1):
        response = workspace.get(
            LIST, query_string={"state": "open", "q": "BULK", "page": page}
        )
        assert response.status_code == 200
        assert re.findall(rf'href="{LIST}/(\d+)"', response.text) == list(
            map(str, expected)
        )
        for delta, label in ((-1, "Vorherige Seite"), (1, "Nächste Seite")):
            if 1 <= page + delta <= 3:
                assert (
                    f'href="{LIST}?state=open&amp;q=BULK&amp;page={page + delta}"'
                    in response.text
                )
                assert label in response.text
            else:
                assert label not in response.text
    assert workspace.get(LIST, query_string={"page": 4}).status_code == 404


@pytest.mark.parametrize("page", ["", "0", "-1", "1.5", "NaN", "9" * 40])
def test_invoice_list_rejects_invalid_page(workspace, page):
    assert workspace.get(LIST, query_string={"page": page}).status_code == 400


@pytest.mark.parametrize("term", ["%", "_", "!", "\\\\"])
def test_invoice_search_treats_wildcards_as_literal(workspace, term):
    expected = _invoice(f"INV-{term}-1")
    _invoice("INV-X-1")
    assert _invoice_links(workspace.get(LIST, query_string={"q": term}).text) == {
        str(expected)
    }


def test_filtered_invoice_list_transfers_only_matching_rows(
    workspace, invoices, monkeypatch
):
    from contextlib import contextmanager

    from psycopg2.extras import RealDictCursor

    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO invoices (community_id, invoice_number, status)
               SELECT %s, 'HISTORY-' || n, 'issued'
               FROM generate_series(1, 12000) AS n""",
            (COMMUNITY,),
        )

    reads = []

    class RecordingCursor(RealDictCursor):
        def fetchall(self):
            rows = super().fetchall()
            if b"FROM invoices " in (self.query or b""):
                reads.append((self.query, len(rows)))
            return rows

    get_connection = db.get_connection

    @contextmanager
    def recording_connection():
        with get_connection() as conn:
            factory = conn.cursor_factory
            conn.cursor_factory = RecordingCursor
            try:
                yield conn
            finally:
                conn.cursor_factory = factory

    monkeypatch.setattr(db, "get_connection", recording_connection)
    response = workspace.get(LIST)
    assert response.status_code == 200
    assert len(_invoice_links(response.text)) == 50
    assert [row_count for _, row_count in reads] == [51]
    reads.clear()
    for state, query, expected in (
        ("paid", "INV-", {str(invoices["paid"])}),
        ("all", "absent", set()),
    ):
        response = workspace.get(LIST, query_string={"state": state, "q": query})
        assert response.status_code == 200
        assert _invoice_links(response.text) == expected
        assert len(reads) == 1
        sql, row_count = reads.pop()
        assert b"LIMIT 51" in sql
        assert row_count == len(expected)


def test_invoice_address_and_participant_id_appear_together(workspace):
    invoice = _invoice("INV-PARTICIPANT")
    for path in (LIST, f"{LIST}/{invoice}"):
        response = workspace.get(path)
        assert response.status_code == 200
        assert "Bärenweg 12" in response.text
        assert PARTICIPANT in response.text


@pytest.mark.parametrize(
    "path", [ROOT + "/billing", ROOT + "/billing?section=payments"]
)
def test_billing_and_payments_skip_invoice_detail_reads(workspace, monkeypatch, path):
    _question(_invoice("INV-WITH-DETAILS"))

    def unexpected(*args, **kwargs):
        pytest.fail("Billing and Payments should load only payment invoice fields")

    monkeypatch.setattr(db, "list_invoice_queries", unexpected)
    monkeypatch.setattr(db, "list_community_invoice_events", unexpected)
    monkeypatch.setattr(db, "list_community_invoices", unexpected)
    assert workspace.get(path).status_code == 200
