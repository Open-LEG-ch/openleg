# SPDX-License-Identifier: AGPL-3.0-or-later
"""Billing-period pages through HTTP and PostgreSQL."""

from datetime import date

import pytest
from psycopg2.extras import Json

import database as db
from tests.test_billing_approval import _draft
from tests.test_leg_invoices_postgres import (
    ADMIN,
    AUDITOR,
    COMMUNITY,
    OTHER,
    PARTICIPANT,
    ROOT,
    _set_session,
    app_module,  # noqa: F401
    interest_database,  # noqa: F401
    workspace,  # noqa: F401
)

pytestmark = pytest.mark.integration
BILLING = ROOT + "/billing"


def _period(community=COMMUNITY, *, month="08", status="draft"):
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO billing_periods
               (community_id, period_start, period_end, status)
               VALUES (%s, %s, %s::timestamptz + interval '1 month', %s)
               RETURNING id""",
            (
                community,
                f"2026-{month}-01 00:00+02",
                f"2026-{month}-01 00:00+02",
                status,
            ),
        )
        return cur.fetchone()["id"]


def test_billing_lists_local_periods_and_links_to_the_detail(workspace):  # noqa: F811
    period = _period()
    foreign = _period(OTHER, month="09")
    url = f"{BILLING}/period/{period}"

    response = workspace.get(BILLING)
    assert response.status_code == 200
    assert "<title>Abrechnungen | OpenLEG</title>" in response.text
    assert f'href="{url}"' in response.text
    assert "August 2026" in response.text
    assert "Entwurf" in response.text
    assert f'href="{BILLING}/period/{foreign}"' not in response.text

    response = workspace.get(url)
    assert response.status_code == 200
    assert "<title>Abrechnung August 2026 | OpenLEG</title>" in response.text
    assert f'href="{BILLING}"' in response.text


def test_period_detail_requires_audit_access(workspace):  # noqa: F811
    url = f"{BILLING}/period/{_period()}"
    _set_session(workspace, PARTICIPANT)
    response = workspace.get(url)
    assert response.status_code == 403
    assert "August 2026" not in response.text
    _set_session(workspace, AUDITOR)
    assert workspace.get(url).status_code == 200


def test_foreign_period_is_indistinguishable_from_missing_period(workspace):  # noqa: F811
    foreign = _period(OTHER, month="09")
    response = workspace.get(f"{BILLING}/period/{foreign}")
    missing = workspace.get(f"{BILLING}/period/999999")
    assert response.status_code == missing.status_code == 404
    assert response.text == missing.text


def _valid_draft(period):
    draft = _draft()
    actors = {
        "building-a": ADMIN,
        "building-b": PARTICIPANT,
        "building-c": AUDITOR,
    }
    reconciliation = draft["reconciliation"]
    for field in ("per_participant", "production_per_participant"):
        reconciliation[field] = {
            actors[key]: value for key, value in reconciliation[field].items()
        }
    reconciliation["participant_registrations"] = {
        actor: db.get_building(actor)["referral_code"] for actor in actors.values()
    }
    draft["billing_policy_snapshot"]["community_id"] = COMMUNITY
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE communities SET status='active', require_dual_control=true "
            "WHERE community_id=%s",
            (COMMUNITY,),
        )
        cur.execute(
            "UPDATE community_members SET role='admin' "
            "WHERE community_id=%s AND building_id=%s",
            (COMMUNITY, AUDITOR),
        )
        cur.execute(
            """UPDATE billing_periods SET input_fingerprint=%s,
                   source_document_ids=%s, reconciliation=%s, billing_policy_snapshot=%s,
                   created_at='2026-08-01 22:30'
               WHERE id=%s""",
            (
                draft["input_fingerprint"],
                Json(draft["source_document_ids"]),
                Json(reconciliation),
                Json(draft["billing_policy_snapshot"]),
                period,
            ),
        )
        cur.executemany(
            """INSERT INTO billing_line_items
                   (billing_period_id, participant_id, item_type, quantity_kwh,
                    unit_price_chf_per_kwh, amount_chf)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            [
                (
                    period,
                    actors[item["participant_id"]],
                    item["item_type"],
                    item["quantity_kwh"],
                    item["unit_price_chf_per_kwh"],
                    item["amount_chf"],
                )
                for item in draft["line_items"]
            ],
        )


def _assert_stage(client, url, stage, label):
    response = client.get(url)
    assert response.status_code == 200
    assert f'id="step-{stage}" aria-current="step"' in response.text
    listing = client.get(BILLING).text
    assert f'href="{url}"' in listing
    assert f">{label}</span>" in listing
    return response.text


def test_period_progress_follows_prepare_approve_deliver_and_pay(workspace):  # noqa: F811
    period = _period()
    _valid_draft(period)
    url = f"{BILLING}/period/{period}"
    draft = _assert_stage(workspace, url, "draft", "Entwurf")
    assert 'id="step-measurements"' in draft
    assert "Messdaten vorhanden" in draft
    assert "2. August 2026" in draft
    assert 'name="confirm_prepare"' in draft
    assert 'name="confirm_approval"' not in draft

    prepare = {"confirm_prepare": "yes", "csrf_token": "csrf-secret"}
    approve = {"confirm_approval": "yes", "csrf_token": "csrf-secret"}
    for action, form in (("prepare", prepare), ("approve", approve)):
        confirmation = {
            key: value for key, value in form.items() if key != "csrf_token"
        }
        assert workspace.post(f"{url}/{action}", data=confirmation).status_code == 400
        assert (
            workspace.post(
                f"{url}/{action}", data={"csrf_token": "csrf-secret"}
            ).status_code
            == 400
        )
    assert workspace.post(url + "/approve", data=approve).status_code == 403
    response = workspace.post(url + "/prepare", data=prepare)
    assert response.status_code == 302
    assert response.location == url + "?prepared=1"

    prepared = _assert_stage(workspace, url, "prepared", "Vorbereitet")
    assert "Vorbereitet durch:" in prepared
    assert ADMIN in prepared
    assert 'id="prepared-at"' in prepared
    assert 'name="confirm_approval"' not in prepared
    assert workspace.post(url + "/approve", data=approve).status_code == 403

    _set_session(workspace, AUDITOR)
    assert 'name="confirm_approval"' in workspace.get(url).text
    response = workspace.post(url + "/approve", data=approve)
    assert response.status_code == 302
    assert response.location == url + "?approved=1"
    approved = _assert_stage(workspace, url, "approved", "Freigegeben")
    assert 'id="approved-at"' in approved
    assert 'name="confirm_approval"' not in approved

    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM invoices WHERE billing_period_id=%s ORDER BY id", (period,)
        )
        invoices = [row["id"] for row in cur.fetchall()]
    for invoice in invoices[:-1]:
        db.prepare_invoice_delivery(invoice, COMMUNITY, AUDITOR)
        db.complete_invoice_delivery(invoice, COMMUNITY, AUDITOR)
    _assert_stage(workspace, url, "approved", "Freigegeben")
    db.prepare_invoice_delivery(invoices[-1], COMMUNITY, AUDITOR)
    db.complete_invoice_delivery(invoices[-1], COMMUNITY, AUDITOR)
    delivered = _assert_stage(workspace, url, "delivered", "Zugestellt")
    assert 'id="delivered-at"' in delivered

    for invoice in invoices:
        db.record_invoice_payment(
            invoice, COMMUNITY, AUDITOR, date(2026, 9, 10), "BANK"
        )
    paid = _assert_stage(workspace, url, "paid", "Bezahlt")
    assert 'id="paid-at"' in paid
    assert 'id="delivered-at"' in paid


def test_period_shows_scoped_checks_and_draft_amounts(workspace):  # noqa: F811
    period = _period()
    _valid_draft(period)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE billing_line_items SET consumption_kwh=1348.5,
                   allocated_kwh=13.37, amount_chf=1234.555
               WHERE billing_period_id=%s AND participant_id=%s""",
            (period, ADMIN),
        )
        cur.execute(
            """UPDATE billing_line_items SET amount_chf=NULL
               WHERE billing_period_id=%s AND participant_id=%s
                 AND item_type='producer_credit'""",
            (period, PARTICIPANT),
        )
        cur.execute(
            """UPDATE billing_line_items SET amount_chf='NaN'
               WHERE billing_period_id=%s AND participant_id=%s""",
            (period, AUDITOR),
        )
        cur.executemany(
            """INSERT INTO metering_points (metering_point_id, community_id)
               VALUES (%s,%s)""",
            [("LOCAL", COMMUNITY), ("FOREIGN", OTHER)],
        )
        cur.executemany(
            """INSERT INTO sdat_veracity_flags
                  (document_id, metering_point_id, direction, kind, window_start)
               VALUES (%s,%s,'consumption','flatline',%s)""",
            [
                ("swisseldex-bkw-2026-01", "LOCAL", "2026-08-04 12:00+02"),
                ("swisseldex-bkw-2026-01", "FOREIGN", "2026-08-04 12:00+02"),
                ("unrelated-document", "LOCAL", "2026-08-04 12:00+02"),
                ("swisseldex-bkw-2026-01", "LOCAL", "2026-09-01 00:00+02"),
            ],
        )
    response = workspace.get(f"{BILLING}/period/{period}")
    assert response.status_code == 200
    for expected in (
        "Prüfungen vor der Freigabe",
        "Richtlinienversion",
        "Version 7",
        "Vollständig abgeglichen",
        "1 vorhanden",
        "1 auffälliges Messfenster",
        "Rechnungsentwürfe",
        "LEG-Verbrauch",
        "Netzanteil",
        "Musterweg 1",
        "Bärenweg 12",
        "13.37 kWh",
        "1’335.13 kWh",
        "1’234.56 CHF",
        "vor MWST",
    ):
        assert expected in response.text
    assert response.text.count("Betrag unlesbar") == 2


def test_empty_and_unapprovable_period_show_help_without_actions(workspace):  # noqa: F811
    assert "Noch keine Abrechnungsperioden vorhanden." in workspace.get(BILLING).text
    url = f"{BILLING}/period/{_period()}"
    response = workspace.get(url)
    for expected in (
        "Keine Messquellen dokumentiert",
        "Keine Richtlinienversion gespeichert",
        "Abweichung prüfen",
        "Keine Quelldokumente vorhanden",
        "Keine Messfenster prüfbar",
        "Noch keine Rechnungsentwürfe vorhanden.",
        "Dieser Entwurf ist nicht freigabebereit",
    ):
        assert expected in response.text
    assert 'name="confirm_prepare"' not in response.text
    assert 'name="confirm_approval"' not in response.text
    conflict = workspace.post(
        url + "/approve",
        data={"confirm_approval": "yes", "csrf_token": "csrf-secret"},
    )
    assert conflict.status_code == 409
    assert "Dieser Abrechnungsentwurf konnte nicht freigegeben werden." in conflict.text
    assert "<title>Abrechnung August 2026 | OpenLEG</title>" in conflict.text


@pytest.mark.parametrize("dual_control", [False, True])
def test_actions_on_foreign_or_missing_period_do_not_leak(workspace, dual_control):  # noqa: F811
    foreign = _period(OTHER)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE communities SET require_dual_control=%s WHERE community_id=%s",
            (dual_control, COMMUNITY),
        )
    for action, field in (
        ("prepare", "confirm_prepare"),
        ("approve", "confirm_approval"),
    ):
        data = {"csrf_token": "csrf-secret", field: "yes"}
        response = workspace.post(f"{BILLING}/period/{foreign}/{action}", data=data)
        missing = workspace.post(f"{BILLING}/period/999999/{action}", data=data)
        assert response.status_code == missing.status_code == 404
        assert response.text == missing.text


@pytest.mark.parametrize("actor", [PARTICIPANT, AUDITOR])
def test_auditor_and_member_cannot_prepare_or_approve(workspace, actor):  # noqa: F811
    period = _period()
    _set_session(workspace, actor)
    for action, field in (
        ("prepare", "confirm_prepare"),
        ("approve", "confirm_approval"),
    ):
        response = workspace.post(
            f"{BILLING}/period/{period}/{action}",
            data={"csrf_token": "csrf-secret", field: "yes"},
        )
        assert response.status_code == 403


def test_draft_uses_only_the_original_registrations_address(workspace):  # noqa: F811
    period = _period()
    _valid_draft(period)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE buildings SET referral_code='replacement', address='Ersatzweg 1'
               WHERE building_id=%s""",
            (PARTICIPANT,),
        )
    response = workspace.get(f"{BILLING}/period/{period}")
    assert response.status_code == 200
    assert PARTICIPANT in response.text
    assert "Ersatzweg 1" not in response.text


def test_billing_periods_are_paginated_in_reverse_window_order(workspace):  # noqa: F811
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO billing_periods (community_id, period_start, period_end)
               SELECT %s, starts, starts + interval '1 month' FROM generate_series(
                   '2026-02-01'::timestamptz, '2030-04-01'::timestamptz, '1 month'
               ) starts RETURNING id""",
            (COMMUNITY,),
        )
        ids = sorted(row["id"] for row in cur.fetchall())
    page = workspace.get(BILLING).text
    assert page.count(f'href="{BILLING}/period/') == 50
    assert f'href="{BILLING}/period/{ids[0]}"' not in page
    assert f'href="{BILLING}?period_before={ids[1]}#billing-periods"' in page
    older = workspace.get(BILLING, query_string={"period_before": ids[1]}).text
    assert older.count(f'href="{BILLING}/period/') == 1
    assert f'href="{BILLING}/period/{ids[0]}"' in older
    assert f'href="{BILLING}#billing-periods"' in older


def test_draft_table_pages_by_participant_and_preserves_each_total(workspace):  # noqa: F811
    period = _period()
    url = f"{BILLING}/period/{period}"
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO billing_line_items
                 (billing_period_id, participant_id, item_type, amount_chf)
               SELECT %s, 'row-' || to_char(n, 'FM000'), 'consumer_charge', 10
               FROM generate_series(1, 51) n""",
            (period,),
        )
        cur.execute(
            """INSERT INTO billing_line_items
                 (billing_period_id, participant_id, item_type, amount_chf)
               VALUES (%s, 'row-050', 'settlement_fee', 1)""",
            (period,),
        )
    page = workspace.get(url).text
    assert page.count('scope="row"') == 50
    assert "11.00 CHF" in page
    assert "row-051" not in page
    assert f'href="{url}?after=row-050#draft-heading"' in page
    rest = workspace.get(url, query_string={"after": "row-050"}).text
    assert rest.count('scope="row"') == 1
    assert "row-051" in rest
    assert "11.00 CHF" not in rest
    assert f'href="{url}#draft-heading"' in rest


@pytest.mark.parametrize(
    ("locale", "list_title", "detail_title"),
    [
        ("fr-CH", "Décomptes", "Décompte août 2026"),
        ("en", "Settlements", "Billing period August 2026"),
        ("it-CH", "Fatturazione", "Conteggio di agosto 2026"),
    ],
)
def test_period_titles_are_localized(
    workspace,  # noqa: F811
    app_module,  # noqa: F811
    monkeypatch,
    locale,
    list_title,
    detail_title,
):
    monkeypatch.setitem(
        app_module.web.config, "SITE_RELEASE_ENABLED", locale != "fr-CH"
    )
    period = _period()
    listing = workspace.get(
        BILLING, query_string={"lang": locale}, follow_redirects=True
    )
    assert f"<title>{list_title} | OpenLEG</title>" in listing.text
    detail = workspace.get(f"{BILLING}/period/{period}")
    assert f"<title>{detail_title} | OpenLEG</title>" in detail.text


def test_period_marks_unknown_legacy_preparation_time(workspace):  # noqa: F811
    period = _period()
    _valid_draft(period)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE billing_periods SET prepared_by=%s WHERE id=%s", (ADMIN, period)
        )
    html = _assert_stage(
        workspace, f"{BILLING}/period/{period}", "prepared", "Vorbereitet"
    )
    assert "Zeitpunkt nicht erfasst" in html
    assert 'id="prepared-at"' not in html
    assert "Keine auffälligen Messfenster" in html


def test_period_authorization_does_not_load_the_community_roster(
    workspace,  # noqa: F811
    monkeypatch,
):
    period = _period()
    _valid_draft(period)
    roster_reads = []
    fetch_roster = db.fetch_community_with_members

    def record_roster_read(*args, **kwargs):
        roster_reads.append(args)
        return fetch_roster(*args, **kwargs)

    monkeypatch.setattr(db, "fetch_community_with_members", record_roster_read)
    assert workspace.get(f"{BILLING}/period/{period}").status_code == 200
    assert roster_reads == []


@pytest.mark.parametrize(
    ("dual_control", "preparer", "may_approve"),
    [
        (True, "system", False),
        (True, None, False),
        (True, ADMIN, False),
        (True, AUDITOR, True),
        (False, "system", True),
    ],
)
def test_period_approval_action_requires_another_human_under_dual_control(
    workspace,  # noqa: F811
    dual_control,
    preparer,
    may_approve,
):
    period = _period()
    _valid_draft(period)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE billing_periods SET prepared_by=%s WHERE id=%s", (preparer, period)
        )
        cur.execute(
            "UPDATE communities SET require_dual_control=%s WHERE community_id=%s",
            (dual_control, COMMUNITY),
        )
    response = workspace.get(f"{BILLING}/period/{period}")
    assert response.status_code == 200
    assert ('name="confirm_approval"' in response.text) is may_approve
    assert ('name="confirm_prepare"' in response.text) is not may_approve


@pytest.mark.parametrize("deliver_all", [True, False])
def test_cancelled_period_keeps_its_recorded_delivery_milestone(workspace, deliver_all):  # noqa: F811
    period = _period()
    _valid_draft(period)
    url = f"{BILLING}/period/{period}"
    assert (
        workspace.post(
            url + "/prepare",
            data={"confirm_prepare": "yes", "csrf_token": "csrf-secret"},
        ).status_code
        == 302
    )
    _set_session(workspace, AUDITOR)
    assert (
        workspace.post(
            url + "/approve",
            data={"confirm_approval": "yes", "csrf_token": "csrf-secret"},
        ).status_code
        == 302
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM invoices WHERE billing_period_id=%s ORDER BY id", (period,)
        )
        invoices = [row["id"] for row in cur.fetchall()]
    for invoice in invoices if deliver_all else invoices[:-1]:
        db.prepare_invoice_delivery(invoice, COMMUNITY, AUDITOR)
        db.complete_invoice_delivery(invoice, COMMUNITY, AUDITOR)
    for invoice in invoices:
        db.cancel_invoice(invoice, COMMUNITY, AUDITOR, "Teststorno")
    html = _assert_stage(workspace, url, "cancelled", "Storniert")
    assert ('id="delivered-at"' in html) is deliver_all
    assert 'id="cancelled-at"' in html
    assert "Keine Zahlung fällig" in html
    delivery_step = html.split('id="step-delivered"', 1)[1].split("</li>", 1)[0]
    assert ("Entfällt" in delivery_step) is not deliver_all
    assert "Ausstehend" not in delivery_step


def test_period_capability_from_another_leg_does_not_grant_access(workspace):  # noqa: F811
    period = _period()
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE community_members SET community_id=%s, role='admin'
               WHERE community_id=%s AND building_id=%s""",
            (OTHER, COMMUNITY, AUDITOR),
        )
    _set_session(workspace, AUDITOR)
    assert workspace.get(f"{BILLING}/period/{period}").status_code == 403


def test_corrected_original_period_stays_closed_with_its_cancellation_time(workspace):  # noqa: F811
    period = _period(status="issued")
    replacement_period = _period(month="09", status="issued")
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO invoices
                   (billing_period_id, community_id, participant_id, invoice_number, status)
               VALUES (%s,%s,%s,'ORIGINAL','issued'), (%s,%s,%s,'REPLACEMENT','issued')
               RETURNING id""",
            (
                period,
                COMMUNITY,
                PARTICIPANT,
                replacement_period,
                COMMUNITY,
                PARTICIPANT,
            ),
        )
        original, replacement = [row["id"] for row in cur.fetchall()]
    db.cancel_invoice(original, COMMUNITY, ADMIN, "Teststorno")
    url = f"{BILLING}/period/{period}"
    before = _assert_stage(workspace, url, "cancelled", "Storniert")
    db.correct_invoice(original, replacement, COMMUNITY, ADMIN, "Ersatzrechnung")
    after = _assert_stage(workspace, url, "cancelled", "Storniert")
    cancellation_time = before.split('id="cancelled-at"', 1)[1].split("</time>", 1)[0]
    assert f'id="cancelled-at"{cancellation_time}</time>' in after
    assert "Keine Zahlung fällig" in after
    _assert_stage(
        workspace, f"{BILLING}/period/{replacement_period}", "approved", "Freigegeben"
    )
