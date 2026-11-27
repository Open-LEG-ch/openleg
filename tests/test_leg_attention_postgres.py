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
        rf'<a href="{ROOT}/{section}"[^>]*>[^<]*<span[^>]*>\s*{count}\s*</span>',
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
    assert f'href="{ROOT}/payments#open-invoices"' in html
    assert f'href="{ROOT}/payments#statement-assignments"' in html
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
    assert "<dt>Warten auf Zuordnung</dt> <dd>1</dd>" in payments.text
    assert len(re.findall(r'action="[^"]*/statements/\d+/assign"', payments.text)) == 1
    _nav_count(payments.text, "payments", 1)
