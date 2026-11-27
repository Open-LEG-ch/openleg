# SPDX-License-Identifier: AGPL-3.0-or-later
"""Workspace attention and ledger through HTTP and PostgreSQL."""

import re
from datetime import datetime
from html import unescape

import pytest
from psycopg2.extras import Json

import dashboard
import database as db
from tests.test_leg_invoices_postgres import (
    ADMIN,
    AUDITOR,
    COMMUNITY,
    OTHER,
    PARTICIPANT,
    ROOT,
    _event,
    _invoice,
    _question,
    _set_session,
    app_module,  # noqa: F401
    interest_database,  # noqa: F401
    workspace,  # noqa: F401
)

pytestmark = pytest.mark.integration
OVERVIEW = f"/leg/dashboard?cid={COMMUNITY}"


@pytest.fixture
def operating(workspace, monkeypatch):  # noqa: F811
    class November(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 27, 12, tzinfo=tz)

    monkeypatch.setattr(dashboard, "datetime", November)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "UPDATE buildings SET email=%s WHERE building_id=%s",
            [(actor + "@example.org", actor) for actor in (ADMIN, AUDITOR)],
        )
        cur.execute(
            "UPDATE communities SET status='active' WHERE community_id=%s",
            (COMMUNITY,),
        )
    return workspace


def test_formation_overview_until_operating_start(workspace):  # noqa: F811
    response = workspace.get(OVERVIEW)
    assert response.status_code == 200
    assert "Anmeldung beim Netzbetreiber" in response.text
    assert "Ausserdem offen" not in response.text
    assert "Abrechnungen 2026" not in response.text


def test_empty_operating_overview_names_next_date(operating):
    response = operating.get(OVERVIEW)
    assert response.status_code == 200
    assert (
        "<title>Derzeit ist nichts offen, die nächste Abrechnungsperiode beginnt am "
        "1. Dezember 2026. | OpenLEG</title>"
    ) in response.text
    assert "Es gibt keine weiteren offenen Aufgaben." in response.text
    assert "Für 2026 liegen noch keine Abrechnungen vor." in response.text
    assert "Anmeldung beim Netzbetreiber" not in response.text
    assert operating.get(ROOT + "/formation").status_code == 200


def _period(month, *, year=2026, community=COMMUNITY, status="issued", preparer=None):
    start = f"{year}-{month:02d}-01"
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO billing_periods (
                   community_id, period_start, period_end, status, prepared_by,
                   reconciliation, source_document_ids
               ) VALUES (
                   %s, %s::timestamp AT TIME ZONE 'Europe/Zurich',
                   (%s::timestamp + interval '1 month') AT TIME ZONE 'Europe/Zurich',
                   %s, %s, '{"difference_kwh": 0}', '["source"]'
               ) RETURNING id""",
            (community, start, start, status, preparer),
        )
        return cur.fetchone()["id"]


def _ledger_row(html, period):
    row = re.search(
        rf'<tr[^>]*data-ledger-period="{period}"[^>]*>(.*?)</tr>', html, re.DOTALL
    )
    assert row
    return [
        unescape(cell.strip())
        for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row[1], re.DOTALL)
    ]


def test_ledger_groups_zurich_months_and_excludes_void_old_and_foreign_invoices(
    operating,
):
    september = _period(9)
    for invoice, period, amount, state, community in (
        ("paid", september, "1071.70", "paid", COMMUNITY),
        ("open", _period(10), "28.30", "delivered", COMMUNITY),
        ("void", _period(8), "999.00", "corrected", COMMUNITY),
        ("old", _period(12, year=2025), "100.00", "paid", COMMUNITY),
        ("foreign", _period(10, community=OTHER), "100.00", "paid", OTHER),
    ):
        invoice_id = _invoice(
            invoice, amount=amount, community=community, period=period
        )
        _event(invoice_id, state, community=community)
    _period(11, community=OTHER, status="draft")
    _invoice("foreign-linked", community=OTHER, participant=ADMIN, period=september)
    html = operating.get(OVERVIEW).text
    assert _ledger_row(html, "2026-11") == ["November", "", "", "", "", ""]
    assert _ledger_row(html, "2026-09") == [
        "September",
        "1",
        "1'071.70",
        "1'071.70",
        "",
        "Bezahlt",
    ]
    assert _ledger_row(html, "2026-10") == [
        "Oktober",
        "1",
        "28.30",
        "",
        "28.30",
        "Zugestellt",
    ]
    assert _ledger_row(html, "2026-01") == ["Januar", "", "", "", "", ""]
    assert _ledger_row(html, "total") == [
        "Total",
        "2",
        "1'100.00",
        "1'071.70",
        "28.30",
        "",
    ]


def test_ledger_marks_month_with_only_cancelled_or_corrected_invoices(operating):
    period = _period(8)
    _event(_invoice("cancelled", amount="18.00", period=period), "cancelled")
    _event(
        _invoice("corrected", amount="30.00", period=period, participant=ADMIN),
        "corrected",
    )

    detail = operating.get(f"{ROOT}/billing/period/{period}")
    assert detail.status_code == 200
    assert "Keine Zahlung fällig" in detail.text
    overview = operating.get(OVERVIEW)
    assert overview.status_code == 200
    assert _ledger_row(overview.text, "2026-08") == [
        "August",
        "",
        "",
        "",
        "",
        "Storniert",
    ]


@pytest.mark.parametrize("amount", [None, "NaN"])
def test_ledger_flags_unreadable_amounts_without_hiding_known_paid_amounts(
    operating, amount
):
    paid = _invoice("paid", amount="0.00", period=_period(8))
    _event(paid, "paid")
    unreadable = _invoice("unreadable", amount=amount, period=_period(9))
    _event(unreadable, "delivered")
    html = operating.get(OVERVIEW).text
    assert "Eine Rechnung ist überfällig und ihr Betrag ist unlesbar." in html
    assert _ledger_row(html, "2026-09") == [
        "September",
        "1",
        "Unlesbar",
        "",
        "Unlesbar",
        "Zugestellt",
    ]
    assert _ledger_row(html, "total") == [
        "Total",
        "2",
        "Unlesbar",
        "0.00",
        "Unlesbar",
        "",
    ]


def _roles(actor, *roles):
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE community_members SET role='member', access_roles=%s
               WHERE community_id=%s AND building_id=%s""",
            (Json(roles), COMMUNITY, actor),
        )


def _dual_control():
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE communities SET require_dual_control=true WHERE community_id=%s",
            (COMMUNITY,),
        )


def _bank_entry(community=COMMUNITY, *, include_ineligible=False):
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO bank_statement_imports
               (community_id, actor_id, source_name, message_type, fingerprint)
               VALUES (%s, %s, 'statement.xml', 'camt.053', repeat('a', 64)) RETURNING id""",
            (community, ADMIN),
        )
        statement_id = cur.fetchone()["id"]
        entries = [("eligible", "ref", "BOOK")]
        if include_ineligible:
            entries.extend([("pending", "ref", "PDNG"), ("blank", "\u00a0", "BOOK")])
        cur.executemany(
            """INSERT INTO bank_statement_entries (
                   statement_import_id, community_id, entry_reference, booking_date,
                   amount, currency, payment_reference, credit_debit_indicator,
                   match_decision, booking_status, amount_basis
               ) VALUES (%s, %s, %s, '2026-11-26', 15, 'CHF', %s,
                         'CRDT', 'unmatched', %s, 'entry')""",
            [
                (statement_id, community, entry, ref, status)
                for entry, ref, status in entries
            ],
        )


def _failed_submission(community=COMMUNITY):
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO vnb_submission_cases (
                   case_id, community_id, created_by, adapter_key, contract_version,
                   payload_fingerprint, state, next_action
               ) VALUES (%s, %s, %s, 'manual', '1', 'fingerprint', 'failed', 'retry')""",
            (community, community, ADMIN),
        )


def _failed_mutation(community=COMMUNITY):
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO vnb_mutation_cases (
                   case_id, mutation_id, community_id, participant_id, created_by,
                   mutation_type, effective_date, source_agreement_id, before_facts,
                   after_facts, adapter_key, contract_version, payload_fingerprint,
                   state, next_action
               ) VALUES (%s, 'mutation', %s, %s, %s, 'change', '2026-11-26', 'contract',
                         '{}', '{}', 'manual', '1', 'fingerprint', 'failed', 'retry')""",
            (community, community, PARTICIPANT, ADMIN),
        )


def _nav_count(html, section, count):
    assert re.search(
        rf'<a href="{ROOT}/{section}(?:\?assignable=1#statement-assignments)?"[^>]*>[^<]*<span[^>]*>\s*{count}\s*</span>',
        html,
    )


def test_period_tasks_respect_roles_and_dual_control(operating):
    _dual_control()
    prepare = _period(9, status="draft")
    approve = _period(10, status="draft", preparer=ADMIN)
    foreign = _period(11, status="draft", preparer=ADMIN, community=OTHER)

    html = operating.get(OVERVIEW).text
    assert (
        "<title>September 2026 wartet auf Ihre Vorbereitung. | OpenLEG</title>" in html
    )
    assert f'href="{ROOT}/billing/period/{prepare}"' in html
    assert f'href="{ROOT}/billing/period/{approve}"' not in html
    _nav_count(html, "billing", 1)
    assert (
        'name="confirm_prepare"'
        in operating.get(f"{ROOT}/billing/period/{prepare}").text
    )

    _roles(AUDITOR, "billing_approver")
    _set_session(operating, AUDITOR)
    html = operating.get(OVERVIEW).text
    assert "<title>Oktober 2026 wartet auf Ihre Freigabe. | OpenLEG</title>" in html
    assert f'href="{ROOT}/billing/period/{approve}"' in html
    assert f'href="{ROOT}/billing/period/{prepare}"' not in html
    assert f'href="{ROOT}/billing/period/{foreign}"' not in html
    _nav_count(html, "billing", 1)
    _nav_count(operating.get(ROOT + "/billing").text, "billing", 1)
    assert (
        'name="confirm_approval"'
        in operating.get(f"{ROOT}/billing/period/{approve}").text
    )

    _roles(PARTICIPANT, "billing_preparer")
    _set_session(operating, PARTICIPANT)
    html = operating.get(OVERVIEW).text
    assert f'href="{ROOT}/billing/period/{prepare}"' in html
    assert f'href="{ROOT}/billing/period/{approve}"' not in html
    _nav_count(html, "billing", 1)


def test_invoice_payment_and_vnb_tasks_match_navigation(operating):
    delivery = _invoice("delivery")
    overdue = _invoice("overdue", amount="1071.70")
    _event(overdue, "delivered")
    question = _question(overdue)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE invoice_queries SET response_due_at='2026-11-26 23:30+00' WHERE id=%s",
            (question,),
        )

    for community in (COMMUNITY, OTHER):
        _bank_entry(community)
        _failed_submission(community)
        _failed_mutation(community)
    _question(_invoice("foreign", community=OTHER), community=OTHER)
    _question(delivery, status="resolved")

    html = operating.get(OVERVIEW).text
    assert "Eine Rechnungsfrage ist bis 27. November 2026 zu beantworten." in html
    assert "Eine Rechnung über CHF 1&#39;071.70 ist überfällig." in html
    assert "Eine Rechnung wartet auf Zustellung." in html
    assert "Eine Bankbuchung wartet auf Zuordnung." in html
    assert "Eine Anmeldung beim Netzbetreiber ist fehlgeschlagen." in html
    assert "Eine Mitgliedermutation beim Netzbetreiber ist fehlgeschlagen." in html
    assert f'href="{ROOT}/invoices/{overdue}#questions"' in html
    assert f'href="{ROOT}/invoices/{delivery}"' in html
    assert f'href="{ROOT}/payments?overdue=1#open-invoices"' in html
    assert f'href="{ROOT}/payments?assignable=1#statement-assignments"' in html
    assert f'href="{ROOT}/formation#vnb-submissions-title"' in html
    assert f'href="{ROOT}/members#vnb-mutations-title"' in html
    for section, count in (
        ("invoices", 2),
        ("payments", 2),
        ("formation", 1),
        ("members", 1),
    ):
        _nav_count(html, section, count)
        _nav_count(operating.get(ROOT + "/" + section).text, section, count)

    _roles(PARTICIPANT, "billing_preparer")
    _set_session(operating, PARTICIPANT)
    html = operating.get(OVERVIEW).text
    _nav_count(html, "invoices", 2)
    _nav_count(html, "payments", 2)
    assert "ist fehlgeschlagen." not in html

    _set_session(operating, AUDITOR)
    html = operating.get(OVERVIEW).text
    assert "Ausserdem offen" in html
    assert "wartet auf" not in html
    assert "ist fehlgeschlagen." not in html
    assert "überfällig." not in html
    assert "Abrechnungen 2026" in html

    _roles(PARTICIPANT, "membership")
    _set_session(operating, PARTICIPANT)
    html = operating.get(OVERVIEW).text
    _nav_count(html, "members", 1)
    assert "Eine Anmeldung beim Netzbetreiber ist fehlgeschlagen." not in html
    assert "Eine Rechnung wartet auf Zustellung." not in html
    assert "Eine Rechnungsfrage ist" not in html
    assert "Eine Bankbuchung wartet auf Zuordnung." not in html
    assert "ist überfällig." not in html
    assert "Abrechnungen 2026" not in html

    _roles(PARTICIPANT, "documents")
    html = operating.get(OVERVIEW).text
    _nav_count(html, "formation", 1)
    assert "Eine Mitgliedermutation beim Netzbetreiber ist fehlgeschlagen." not in html
    assert "Abrechnungen 2026" not in html


def test_legacy_ledger_uses_the_frozen_billing_month(operating):
    _invoice("legacy", period_start="2026-08-31T22:00:00+00:00")
    _invoice("invalid", period_start="2026-13-32")
    html = operating.get(OVERVIEW).text
    assert _ledger_row(html, "2026-09") == [
        "September",
        "1",
        "12.30",
        "",
        "12.30",
        "Freigegeben",
    ]
    assert _ledger_row(html, "total") == ["Total", "1", "12.30", "", "12.30", ""]


def test_next_payment_date_when_delivered_invoice_is_not_overdue(operating):
    due = _invoice("due", due_date="2026-11-29")
    _event(due, "delivered")
    html = operating.get(OVERVIEW).text
    assert "die nächste Zahlung ist am 29. November 2026 fällig." in html
    assert "ist überfällig." not in html


@pytest.mark.parametrize("difference", ["invalid", "0.25"])
def test_approver_sees_only_drafts_ready_for_review(operating, difference):
    invalid = _period(9, status="draft", preparer=ADMIN)
    ready = _period(10, status="draft", preparer=ADMIN)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE billing_periods SET reconciliation=%s WHERE id=%s",
            (
                Json({"difference_kwh": 0, "nested": {"difference_kwh": difference}}),
                invalid,
            ),
        )
        cur.execute(
            "UPDATE billing_periods SET reconciliation=%s WHERE id=%s",
            (
                Json(
                    {
                        "difference_kwh": 0,
                        "precision_policy": {},
                        "nested": {"difference_kwh": "0.25"},
                    }
                ),
                ready,
            ),
        )
    _roles(AUDITOR, "billing_approver")
    _set_session(operating, AUDITOR)
    html = operating.get(OVERVIEW).text
    assert f'href="{ROOT}/billing/period/{ready}"' in html
    assert f'href="{ROOT}/billing/period/{invalid}"' not in html
    _nav_count(html, "billing", 1)


def test_overdue_invoice_precedes_question_with_time_to_answer(operating):
    invoice = _invoice("overdue")
    _event(invoice, "delivered")
    question = _question(invoice)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE invoice_queries SET response_due_at='2026-11-30 12:00+00' WHERE id=%s",
            (question,),
        )
    html = operating.get(OVERVIEW).text
    assert (
        "<title>Eine Rechnung über CHF 12.30 ist überfällig. | OpenLEG</title>" in html
    )
    assert "Eine Rechnungsfrage ist bis 30. November 2026 zu beantworten." in html


@pytest.mark.parametrize("preparer_issue", ["unconfirmed", "same-mailbox"])
def test_approval_attention_agrees_with_period_page_for_ineligible_preparer(
    operating, preparer_issue
):
    _dual_control()
    period = _period(10, status="draft", preparer=ADMIN)
    _roles(AUDITOR, "billing_approver")
    _set_session(operating, AUDITOR)
    with db.get_connection() as conn, conn.cursor() as cur:
        if preparer_issue == "unconfirmed":
            cur.execute(
                "UPDATE community_members SET status='invited' WHERE community_id=%s AND building_id=%s",
                (COMMUNITY, ADMIN),
            )
        else:
            cur.execute(
                "UPDATE buildings SET email=%s WHERE building_id=%s",
                (AUDITOR.upper() + "@example.org", ADMIN),
            )
    url = f"{ROOT}/billing/period/{period}"
    overview = operating.get(OVERVIEW)
    detail = operating.get(url)
    assert overview.status_code == detail.status_code == 200
    assert 'name="confirm_approval"' not in detail.text
    assert f'href="{url}"' not in overview.text


def test_bank_attention_matches_payment_summary_and_assignment_forms(operating):
    invoice = _invoice("open", amount="15.00", due_date="2026-12-04")
    _event(invoice, "delivered")
    _bank_entry(include_ineligible=True)
    _bank_entry(OTHER)

    overview = operating.get(OVERVIEW)
    assert overview.status_code == 200
    assert (
        "<title>Eine Bankbuchung wartet auf Zuordnung. | OpenLEG</title>"
        in overview.text
    )
    _nav_count(overview.text, "payments", 1)

    payments = operating.get(ROOT + "/payments")
    assert payments.status_code == 200
    assert "<dt>In diesem Import zur Zuordnung bereit</dt> <dd>1</dd>" in payments.text
    assert len(re.findall(r'action="[^"]*/statements/\d+/assign"', payments.text)) == 1
    _nav_count(payments.text, "payments", 1)


def _extra_statement(entries, fingerprint="b"):
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO bank_statement_imports
               (community_id, actor_id, source_name, message_type, fingerprint)
               VALUES (%s, %s, 'extra.xml', 'camt.053', repeat(%s, 64)) RETURNING id""",
            (COMMUNITY, ADMIN, fingerprint),
        )
        statement_id = cur.fetchone()["id"]
        cur.executemany(
            """INSERT INTO bank_statement_entries (
                   statement_import_id, community_id, entry_reference, booking_date,
                   amount, currency, payment_reference, credit_debit_indicator,
                   match_decision, is_reversal, booking_status, amount_basis
               ) VALUES (%s, %s, %s, '2026-11-26', 15, %s, %s,
                         %s, 'unmatched', %s, %s, %s)""",
            [
                (statement_id, COMMUNITY, str(i), *entry)
                for i, entry in enumerate(entries)
            ],
        )
        return statement_id


def test_bank_aggregate_matches_python_eligibility(operating):
    from payment_reconciliation import payment_entry_refusal

    whitespace = "".join(chr(code) for code in range(0x110000) if chr(code).isspace())
    for i, (currency, reference, direction, reversal, booking, basis) in enumerate(
        [
            ("EUR", "ref", "CRDT", False, "BOOK", "entry"),
            ("CHF", "ref", "DBIT", False, "BOOK", "entry"),
            ("CHF", "ref", "CRDT", False, "PDNG", "entry"),
            ("CHF", "ref", "CRDT", False, "BOOK", None),
            ("CHF", None, "CRDT", False, "BOOK", "entry"),
            ("CHF", "ref", "CRDT", True, "BOOK", "entry"),
            *[
                ("CHF", ref, "CRDT", False, "BOOK", "reconciled_batch")
                for ref in (
                    "",
                    " ",
                    "\t\n\r",
                    "\u001c",
                    "\u00a0\u2003",
                    whitespace,
                    "\u200b",
                    "\ufeff",
                    "\u00a0ref\t",
                )
            ],
            ("CHF", "ref", "CRDT", False, None, "entry"),
        ]
    ):
        _extra_statement(
            [(currency, reference, direction, reversal, booking, basis)],
            fingerprint=f"{i:x}",
        )
        allowed = (
            payment_entry_refusal(
                {
                    "currency": currency,
                    "payment_reference": reference,
                    "credit_debit_indicator": direction,
                    "is_reversal": reversal,
                    "booking_status": booking,
                    "amount_basis": basis,
                }
            )
            is None
        )
        assert db.get_bank_statement_summary(COMMUNITY, None, ADMIN)[
            "unassigned"
        ] == int(allowed), (currency, reference, direction, reversal, booking, basis)
    html = operating.get(OVERVIEW).text
    _nav_count(html, "payments", 3)


def test_bank_aggregate_counts_servicer_references_on_distinct_accounts(operating):
    invoice = _invoice("open", amount="15.00", due_date="2026-12-04")
    _event(invoice, "delivered")
    statement = _extra_statement(
        [("CHF", "", "CRDT", False, "BOOK", "entry")] * 3,
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.executemany(
            """UPDATE bank_statement_entries
               SET entry_reference='shared', bank_account=%s, servicer_reference=%s
               WHERE statement_import_id=%s AND entry_reference=%s""",
            [
                ("CH0000000000000000001", "own-reference", statement, "0"),
                ("CH0000000000000000002", "own-reference", statement, "1"),
                ("CH0000000000000000003", "\u00a0", statement, "2"),
            ],
        )
    summary = db.get_bank_statement_summary(COMMUNITY, None, ADMIN)
    assert summary["unassigned"] == 2
    _nav_count(operating.get(OVERVIEW).text, "payments", 2)
    response = operating.get(ROOT + "/payments?assignable=1")
    assert response.status_code == 200
    assert (
        len(
            re.findall(
                r'action="[^"]*/statements/\d+/assign(?:\?[^"]*)?"', response.text
            )
        )
        == 2
    )


def test_navigation_counts_bank_assignments_in_one_sql_aggregate(
    operating, monkeypatch
):
    from contextlib import contextmanager

    _bank_entry(include_ineligible=True)
    get_connection = db.get_connection
    queries = []
    named_cursors = []

    class RecordingCursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def __enter__(self):
            self.cursor.__enter__()
            return self

        def __exit__(self, *args):
            return self.cursor.__exit__(*args)

        def execute(self, query, *args):
            queries.append(str(query))
            return self.cursor.execute(query, *args)

        def __iter__(self):
            return iter(self.cursor)

        def __getattr__(self, name):
            return getattr(self.cursor, name)

    class RecordingConnection:
        def __init__(self, conn):
            self.conn = conn

        def cursor(self, *args, **kwargs):
            if kwargs.get("name"):
                named_cursors.append(kwargs["name"])
            return RecordingCursor(self.conn.cursor(*args, **kwargs))

    @contextmanager
    def record():
        with get_connection() as conn:
            yield RecordingConnection(conn)

    monkeypatch.setattr(db, "get_connection", record)
    response = operating.get(ROOT + "/billing")
    assert response.status_code == 200
    _nav_count(response.text, "payments", 1)
    count_queries = [q for q in queries if "bank_statement_entries" in q]
    assert len(count_queries) == 1
    assert "count(" in count_queries[0].lower()
    assert named_cursors == []


def test_system_draft_requires_human_preparation_under_dual_control(operating):
    _dual_control()
    draft = _period(10, status="draft", preparer="system")
    html = operating.get(OVERVIEW).text
    assert "<title>Oktober 2026 wartet auf Ihre Vorbereitung. | OpenLEG</title>" in html
    detail = operating.get(f"{ROOT}/billing/period/{draft}").text
    assert 'name="confirm_prepare"' in detail
    assert 'name="confirm_approval"' not in detail

    _roles(AUDITOR, "billing_approver")
    _set_session(operating, AUDITOR)
    html = operating.get(OVERVIEW).text
    assert f'href="{ROOT}/billing/period/{draft}"' not in html


def test_payments_shows_leg_wide_count_when_latest_import_has_no_eligible_entries(
    operating,
):
    _bank_entry()
    _extra_statement([("CHF", "ref", "CRDT", False, "PDNG", "entry")])
    _nav_count(operating.get(OVERVIEW).text, "payments", 1)
    html = operating.get(ROOT + "/payments").text
    _nav_count(html, "payments", 1)
    assert re.search(
        r'id="statement-heading"[^>]*>\s*Zahlungsabgleich prüfen\s*<span[^>]*>1</span>',
        html,
    )
    assert "<dt>In diesem Import zur Zuordnung bereit</dt> <dd>0</dd>" in html


def test_overdue_task_filters_before_pagination(operating):
    for index in range(51):
        _event(_invoice(f"OVERDUE-{index:02}"), "delivered")
    for index in range(51):
        _event(_invoice(f"FUTURE-{index:02}", due_date="2026-11-27"), "delivered")

    href = ROOT + "/payments?overdue=1#open-invoices"
    assert f'href="{href}"' in operating.get(OVERVIEW).text
    first = operating.get(href).text
    assert "OVERDUE-50" in first
    assert "OVERDUE-00" not in first
    assert "FUTURE-" not in first
    next_page = re.search(
        r'href="([^"]*overdue=1[^"]*invoice_before=[^"]*#open-invoices)"', first
    )
    assert next_page
    older = operating.get(unescape(next_page[1])).text
    assert "OVERDUE-00" in older
    assert "FUTURE-" not in older
    assert f'href="{href}"' in older


@pytest.mark.parametrize("job_status", [None, "pending", "failed"])
def test_delivery_task_matches_invoice_detail_actions(operating, job_status):
    invoice = _invoice("ISSUED")
    if job_status:
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO invoice_delivery_jobs (invoice_id, community_id, delivery_method, status)
                   VALUES (%s, %s, 'email', %s)""",
                (invoice, COMMUNITY, job_status),
            )
    url = f"{ROOT}/invoices/{invoice}"
    html = operating.get(OVERVIEW).text
    detail = operating.get(url).text
    task, action = (
        ("Eine Rechnungszustellung muss geprüft werden.", "Zustellung prüfen")
        if job_status
        else ("Eine Rechnung wartet auf Zustellung.", "Zustellen")
    )
    assert f"<title>{task} | OpenLEG</title>" in html
    assert re.search(rf'href="{url}"[^>]*>{action}</a>', html)
    assert (
        "Zustellung als erfolgt bestätigen" if job_status else ">Zustellen</button>"
    ) in detail


def test_overdue_payments_keeps_bank_assignment_outside_the_invoice_filter(operating):
    invoice = _invoice("UNPAID-NOT-DUE", amount="15.00", due_date="2026-12-04")
    _event(invoice, "delivered")
    payments_url = ROOT + "/payments"
    without_bank_task = operating.get(payments_url + "?overdue=1").text
    assert f'href="{payments_url}#statement-assignments"' in without_bank_task
    _bank_entry()

    overdue = operating.get(payments_url + "?overdue=1")
    assert overdue.status_code == 200
    assert 'name="invoice_id"' not in overdue.text
    assert f'id="open-invoice-{invoice}"' not in overdue.text
    assert 'id="statement-assignments"' not in overdue.text
    assert "Stellen Sie die passende Rechnung zuerst zu." not in overdue.text
    assert f'href="{payments_url}?assignable=1#statement-assignments"' in overdue.text

    regular = operating.get(payments_url)
    assert regular.status_code == 200
    assert f'<option value="{invoice}">UNPAID-NOT-DUE' in regular.text
    assert f'id="open-invoice-{invoice}"' in regular.text
    assert len(re.findall(r'action="[^"]*/statements/\d+/assign"', regular.text)) == 1


def test_bank_task_finds_older_assignable_entry_behind_pending_entries(operating):
    _event(_invoice("AVAILABLE", amount="15.00", due_date="2026-12-04"), "delivered")
    _bank_entry()
    _extra_statement([("CHF", "ref", "CRDT", False, "PDNG", "entry")] * 200)

    _bank_entry(OTHER)

    regular = operating.get(ROOT + "/payments")
    assert not re.search(r'action="[^"]*/statements/\d+/assign"', regular.text)

    url = ROOT + "/payments?assignable=1#statement-assignments"
    html = operating.get(OVERVIEW).text
    assert re.search(rf'href="{re.escape(url)}"[^>]*>Zahlungsabgleich prüfen</a>', html)
    assert re.search(
        rf'href="{re.escape(url)}"[^>]*>Zahlungen\s*<span[^>]*>1</span>', html
    )
    page = operating.get(url)
    assert page.status_code == 200
    assert (
        len(
            re.findall(r'action="[^"]*/statements/\d+/assign\?assignable=1"', page.text)
        )
        == 1
    )
    assert "Der Auszug weist keine definitive Buchung aus." not in page.text


def test_assignable_statement_filter_survives_pagination(operating, monkeypatch):
    monkeypatch.setattr(dashboard, "_STATEMENT_REVIEW_PAGE_SIZE", 1)
    invoice = _invoice("AVAILABLE", amount="15.00", due_date="2026-12-04")
    _event(invoice, "delivered")
    _bank_entry()
    _extra_statement([("CHF", "ref", "CRDT", False, "PDNG", "entry")])
    _extra_statement(
        [("CHF", "newer", "CRDT", False, "BOOK", "entry")], fingerprint="c"
    )

    url = ROOT + "/payments?assignable=1#statement-assignments"
    first = operating.get(url)
    older = re.search(
        r'href="([^"]*assignable=1[^"]*statement_before=[^"]*#payments)"', first.text
    )
    assert older
    second = operating.get(unescape(older[1]))
    assert second.status_code == 200
    assert re.search(
        r'action="[^"]*/statements/\d+/assign\?assignable=1(?:&amp;|")', second.text
    )
    assert f'href="{ROOT}/payments?assignable=1#payments"' in second.text
    assert "Der Auszug weist keine definitive Buchung aus." not in second.text

    action = re.search(
        r'action="([^"]*/statements/\d+/assign\?assignable=1[^"]*)"', second.text
    )
    assert action
    response = operating.post(
        unescape(action[1]),
        data={"csrf_token": "csrf-secret", "invoice_id": invoice, "confirm": "yes"},
    )
    assert response.status_code == 303
    assert "assignable=1" in response.location
    review = operating.get(response.location).text
    assert "Mögliche Doppelzahlung." in review
    assert f'action="{action[1]}"' in review
    signed = re.search(r'name="duplicate_review" value="([^"]+)"', review)
    assert signed
    confirmed = operating.post(
        unescape(action[1]),
        data={
            "csrf_token": "csrf-secret",
            "invoice_id": invoice,
            "confirm": "yes",
            "confirm_distinct": "yes",
            "duplicate_review": unescape(signed[1]),
        },
    )
    assert confirmed.status_code == 303
    assert "assignable=1" in confirmed.location
    assert "Zahlung zugeordnet" in operating.get(confirmed.location).text


def test_overdue_bank_link_finds_eligible_booking_behind_pending_entries(operating):
    invoice = _invoice("AVAILABLE", amount="15.00", due_date="2026-12-04")
    _event(invoice, "delivered")
    _bank_entry()
    _extra_statement([("CHF", "ref", "CRDT", False, "PDNG", "entry")] * 200)

    overdue = operating.get(ROOT + "/payments?overdue=1")
    link = re.search(r'href="([^"]+)"[^>]*>Zahlungsabgleich prüfen</a>', overdue.text)
    assert link
    assignment = operating.get(unescape(link[1]))
    assert f'<option value="{invoice}">AVAILABLE' in assignment.text
    assert link[1] == ROOT + "/payments?assignable=1#statement-assignments"


@pytest.mark.parametrize("page", ["formation", "members"])
def test_vnb_task_destination_keeps_older_failure_inside_page_limit(operating, page):
    with db.get_connection() as conn, conn.cursor() as cur:
        if page == "formation":
            _failed_submission()
            cur.execute(
                """UPDATE vnb_submission_cases SET response_status='OLDER-FAILURE',
                          created_at='2020-01-01' WHERE community_id=%s""",
                (COMMUNITY,),
            )
            cur.execute(
                """INSERT INTO vnb_submission_cases (
                       case_id, community_id, created_by, adapter_key, contract_version,
                       payload_fingerprint, state, next_action, response_status, created_at
                   )
                   SELECT 'submission-' || n, %s, %s, 'manual', '1', 'fingerprint-' || n,
                          'rejected', 'review_rejection', 'RECENT-' || n,
                          timestamptz '2026-01-01' + n * interval '1 day'
                   FROM generate_series(1, 20) AS n""",
                (COMMUNITY, ADMIN),
            )
        else:
            _failed_mutation()
            cur.execute(
                """UPDATE vnb_mutation_cases SET response_status='OLDER-FAILURE',
                          created_at='2020-01-01' WHERE community_id=%s""",
                (COMMUNITY,),
            )
            cur.execute(
                """INSERT INTO vnb_mutation_cases (
                       case_id, mutation_id, community_id, participant_id, created_by,
                       mutation_type, effective_date, source_agreement_id, before_facts,
                       after_facts, adapter_key, contract_version, payload_fingerprint,
                       state, next_action, response_status, created_at
                   )
                   SELECT 'case-' || n, 'mutation-' || n, %s, %s, %s, 'change',
                          '2026-11-26', 'contract', '{}', '{}', 'manual', '1',
                          'fingerprint-' || n, 'rejected', 'review_rejection',
                          'RECENT-' || n, timestamptz '2026-01-01' + n * interval '1 day'
                   FROM generate_series(1, 100) AS n""",
                (COMMUNITY, PARTICIPANT, ADMIN),
            )

    anchor = "vnb-submissions-title" if page == "formation" else "vnb-mutations-title"
    url = f"{ROOT}/{page}#{anchor}"
    assert f'href="{url}"' in operating.get(OVERVIEW).text
    detail = operating.get(url).text
    newest = 20 if page == "formation" else 100
    assert "OLDER-FAILURE" in detail
    assert detail.index("OLDER-FAILURE") < detail.index(f"RECENT-{newest}")
    assert detail.index(f"RECENT-{newest}") < detail.index(f"RECENT-{newest - 1}")
    assert "RECENT-1<" not in detail

    # Retry decisions and API callers still need the chronological store read.
    list_cases = (
        db.list_vnb_submission_cases if page == "formation" else db.list_vnb_mutations
    )
    assert list_cases(COMMUNITY)[0]["response_status"] == f"RECENT-{newest}"
