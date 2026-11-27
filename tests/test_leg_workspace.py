# SPDX-License-Identifier: AGPL-3.0-or-later
"""HTTP contracts for the LEG administration workspace."""

import re
from datetime import UTC, date, datetime

import pytest

from tests.test_dashboard_access_routes import _set_session, app_module  # noqa: F401
from tests.test_leg_dashboard import STATUS

ROOT = "/leg/community/c0ffee"
OVERVIEW = "/leg/dashboard?cid=c0ffee"


@pytest.fixture
def workspace(app_module, monkeypatch):  # noqa: F811
    status = {
        **STATUS,
        "status": "active",
        "members": [
            *STATUS["members"],
            {"building_id": "reader", "role": "member", "status": "confirmed"},
            {
                "building_id": "auditor",
                "role": "member",
                "status": "confirmed",
                "access_roles": ["auditor"],
            },
        ],
    }
    monkeypatch.setattr(
        app_module.dashboard_module.formation_wizard,
        "get_community_status",
        lambda cid, _bid: status if cid == "c0ffee" else None,
    )
    for operation in (
        "list_leg_documents",
        "list_correspondence",
        "list_community_billing_periods",
        "list_community_invoice_payment_rows",
        "list_billing_policies",
    ):
        monkeypatch.setattr(app_module.db, operation, lambda _cid: [])
    monkeypatch.setattr(
        app_module.db, "page_community_invoices", lambda _cid, **_filters: []
    )
    monkeypatch.setattr(
        app_module.db, "list_bank_statement_entries", lambda _cid, **_filters: []
    )
    monkeypatch.setattr(app_module.db, "get_battery", lambda _cid: None)
    return app_module.web.test_client(), status


def test_member_list_has_its_own_page(workspace):
    client, _ = workspace
    _set_session(client, "b-admin")
    response = client.get(ROOT + "/members")
    assert response.status_code == 200
    assert b"Mitglieder dieser Stromgemeinschaft" in response.data
    assert b'aria-current="page"' in response.data
    assert b"Mitglieder dieser Stromgemeinschaft" not in client.get(OVERVIEW).data


def test_each_workspace_page_has_its_own_title(workspace, monkeypatch):
    from dashboard import formation_wizard

    client, _ = workspace
    monkeypatch.setattr(
        formation_wizard,
        "get_user_communities",
        lambda _bid: [
            {
                "community_id": "c0ffee",
                "name": "LEG Musterweg",
                "member_status": "confirmed",
            }
        ],
    )
    expected = {
        OVERVIEW: "LEG-Dashboard | OpenLEG",
        ROOT + "/members": "Mitglieder · LEG Musterweg | OpenLEG",
        ROOT + "/correspondence": "Korrespondenz · LEG Musterweg | OpenLEG",
        ROOT + "/formation": "Gründung · LEG Musterweg | OpenLEG",
        ROOT + "/settings": "Einstellungen · LEG Musterweg | OpenLEG",
        ROOT + "/payments": "Zahlungen · LEG Musterweg | OpenLEG",
        ROOT + "/invoices": "Rechnungen | OpenLEG",
        ROOT + "/billing": "Abrechnung · LEG Musterweg | OpenLEG",
        ROOT + "/billing-policy": "Abrechnungs-Richtlinie · LEG Musterweg | OpenLEG",
    }

    _set_session(client, "b-admin")
    titles = {}
    for path in expected:
        response = client.get(path)
        assert response.status_code == 200
        titles[path] = re.findall(r"<title>(.*?)</title>", response.text)
    assert titles == {path: [title] for path, title in expected.items()}
    assert len(set(expected.values())) == len(expected)

    # Invitation and error views keep the generic dashboard title.
    fallback = "<title>LEG-Dashboard | OpenLEG</title>"
    for building_id in ("b-guest", "outsider"):
        _set_session(client, building_id)
        assert fallback in client.get(OVERVIEW).text
    refused = client.post(
        ROOT + "/invite",
        data={"csrf_token": "csrf-secret", "invite_email": "new@example.org"},
    )
    assert refused.status_code == 400
    assert fallback in refused.text


@pytest.mark.parametrize(
    ("page", "marker"),
    [
        ("correspondence", "Eintrag hinzufügen"),
        ("formation", "Anmeldung beim Netzbetreiber"),
        ("settings", "Quartierakku speichern"),
        ("payments", "Bankzahlungen in der Abrechnung öffnen"),
        ("invoices", "Noch keine freigegebenen Rechnungen vorhanden."),
        ("billing", "Freigegebene Rechnungen"),
        ("billing-policy", "Neue Version erfassen"),
    ],
)
def test_workspace_pages_show_their_own_content(workspace, page, marker):
    client, _ = workspace
    _set_session(client, "b-admin")
    response = client.get(f"{ROOT}/{page}")
    assert response.status_code == 200
    assert marker in response.text
    assert 'aria-label="LEG-Verwaltung"' in response.text
    assert response.headers["Cache-Control"] == "no-store"


def test_formation_moves_at_operating_start(workspace):
    client, status = workspace
    _set_session(client, "b-admin")
    marker = "Anmeldung beim Netzbetreiber"
    assert marker not in client.get(OVERVIEW).text
    active_formation = client.get(ROOT + "/formation").text
    assert marker in active_formation
    assert f'action="{ROOT}/start-formation"' not in active_formation
    status["status"] = "dso_approved"
    status["formation_started_at"] = "2020-01-01"
    pending_formation = client.get(OVERVIEW).text
    assert marker in pending_formation
    assert f'action="{ROOT}/start-formation"' not in pending_formation
    status["status"] = "interested"
    status["formation_started_at"] = None
    assert f'action="{ROOT}/start-formation"' in client.get(OVERVIEW).text
    assert marker not in client.get(ROOT + "/formation").text


@pytest.mark.parametrize(
    ("leg_status", "page", "empty_state"),
    [
        ("signatures_pending", OVERVIEW, "Anmeldepaket vorbereiten"),
        (
            "active",
            ROOT + "/formation",
            "Das Anmeldepaket kann vorbereitet werden",
        ),
    ],
)
def test_formation_shows_unavailable_submission_data(
    workspace, monkeypatch, leg_status, page, empty_state
):
    import dashboard

    client, status = workspace
    status["status"] = leg_status
    _set_session(client, "b-admin")
    monkeypatch.setattr(dashboard.db, "is_db_available", lambda: True)
    monkeypatch.setattr(dashboard.db, "list_vnb_mutations", lambda _cid: [])
    monkeypatch.setattr(dashboard.db, "list_vnb_submission_cases", lambda _cid: [])
    assert empty_state in client.get(page).text

    def unavailable(_cid):
        raise dashboard.db.VnbExchangeStoreError("submission read unavailable")

    monkeypatch.setattr(dashboard.db, "list_vnb_submission_cases", unavailable)
    response = client.get(page)
    assert response.status_code == 200
    assert "Der Austausch mit dem Netzbetreiber ist derzeit nicht verfügbar." in (
        response.text
    )
    assert empty_state not in response.text
    assert f'action="{ROOT}/vnb-submissions"' not in response.text


@pytest.mark.parametrize(
    "page", ["billing", "payments", "invoices", "settings", "billing-policy"]
)
def test_plain_member_cannot_open_billing_or_admin_pages(workspace, page):
    client, _ = workspace
    _set_session(client, "reader")
    assert f'href="{ROOT}/{page}"' not in client.get(OVERVIEW).text
    assert client.get(f"{ROOT}/{page}").status_code == 403


@pytest.mark.parametrize(
    "page",
    [
        "members",
        "correspondence",
        "formation",
        "settings",
        "payments",
        "invoices",
        "billing",
        "billing-policy",
    ],
)
def test_workspace_pages_require_confirmed_membership(workspace, page):
    client, _ = workspace
    assert client.get(f"{ROOT}/{page}").status_code == 401
    for building_id in ("b-guest", "outsider"):
        _set_session(client, building_id)
        assert client.get(f"{ROOT}/{page}").status_code == 403


def test_auditor_can_open_billing_links_but_cannot_change_policy(workspace):
    client, _ = workspace
    _set_session(client, "auditor")
    response = client.get(OVERVIEW)
    for page in ("billing", "payments", "invoices"):
        assert f'href="{ROOT}/{page}"' in response.text
        assert client.get(f"{ROOT}/{page}").status_code == 200
    assert 'name="statement"' not in client.get(ROOT + "/billing").text
    assert f'href="{ROOT}/billing-policy"' not in client.get(ROOT + "/settings").text
    assert client.get(ROOT + "/billing-policy").status_code == 403


def test_leg_switcher_lists_confirmed_memberships_only(workspace, monkeypatch):
    from dashboard import formation_wizard

    client, _ = workspace
    _set_session(client, "b-admin")
    monkeypatch.setattr(
        formation_wizard,
        "get_user_communities",
        lambda _bid: [
            {
                "community_id": "c0ffee",
                "name": "Current LEG",
                "member_status": "confirmed",
            },
            {
                "community_id": "second",
                "name": "Second LEG",
                "member_status": "confirmed",
            },
            {
                "community_id": "invited",
                "name": "Invitation",
                "member_status": "invited",
            },
            {
                "community_id": "rejected",
                "name": "Rejected LEG",
                "member_status": "rejected",
            },
        ],
    )

    response = client.get(OVERVIEW)
    assert response.status_code == 200
    assert 'href="/leg/dashboard?cid=c0ffee" aria-current="true"' in response.text
    assert 'href="/leg/dashboard?cid=second"' in response.text
    assert 'href="/leg/dashboard?cid=invited"' not in response.text
    assert "Rejected LEG" not in response.text


def test_only_workspace_pages_that_need_correspondence_load_it(workspace, monkeypatch):
    import dashboard

    client, status = workspace
    journal_reads = []

    def list_correspondence(community_id):
        journal_reads.append(community_id)
        return []

    monkeypatch.setattr(dashboard.db, "list_correspondence", list_correspondence)
    _set_session(client, "reader")
    assert client.get(ROOT + "/payments").status_code == 403
    _set_session(client, "b-admin")
    for path in (OVERVIEW, ROOT + "/members", ROOT + "/formation", ROOT + "/payments"):
        assert client.get(path).status_code == 200
    assert journal_reads == []

    assert client.get(ROOT + "/correspondence").status_code == 200
    assert journal_reads == ["c0ffee"]
    status["status"] = "dso_submitted"
    assert client.get(OVERVIEW).status_code == 200
    assert journal_reads == ["c0ffee", "c0ffee"]


def test_billing_policy_marks_settings_as_its_parent_section(workspace):
    client, _ = workspace
    _set_session(client, "b-admin")
    response = client.get(ROOT + "/billing-policy")
    assert response.status_code == 200
    assert 'aria-current="page"' not in response.text
    settings_link = response.text.split(f'href="{ROOT}/settings"', 1)[1].split(">", 1)[
        0
    ]
    assert 'aria-current="true"' in settings_link


@pytest.mark.parametrize(
    ("leg_status", "destination", "other_page"),
    [
        ("active", ROOT + "/formation", OVERVIEW),
        ("signatures_pending", OVERVIEW, ROOT + "/formation"),
    ],
)
def test_formation_feedback_is_shown_only_on_its_destination(
    workspace, monkeypatch, leg_status, destination, other_page
):
    import dashboard

    client, status = workspace
    _set_session(client, "b-admin")
    status["status"] = leg_status
    other_status = {**status, "community_id": "other", "name": "Other LEG"}
    monkeypatch.setattr(
        dashboard.formation_wizard,
        "get_community_status",
        lambda cid, _bid: {"c0ffee": status, "other": other_status}.get(cid),
    )
    message = "Die Gründung lässt sich nur einmal"
    monkeypatch.setattr(
        dashboard,
        "leg_start_formation",
        lambda _cid, _bid: {"error": message},
    )
    result = client.post(ROOT + "/start-formation", data={"csrf_token": "csrf-secret"})
    assert result.location == destination
    assert message not in client.get("/leg/dashboard?cid=other").text
    assert message not in client.get(other_page).text
    assert message in client.get(result.location).text
    assert message not in client.get(result.location).text


def test_payments_shortcut_jumps_to_a_visible_billing_section(workspace):
    client, _ = workspace
    _set_session(client, "auditor")
    assert (
        f'href="{ROOT}/billing?section=payments#payments"'
        in client.get(ROOT + "/payments").text
    )
    assert 'id="payments"' in client.get(ROOT + "/billing").text


def test_billing_invoice_bookmark_links_to_the_invoice_list(workspace):
    client, _ = workspace
    _set_session(client, "auditor")
    billing = client.get(ROOT + "/billing").text
    invoice_section = billing.split('<section id="invoices"', 1)[1].split(
        "</section>", 1
    )[0]
    assert f'href="{ROOT}/invoices"' in invoice_section
    assert client.get(ROOT + "/invoices").status_code == 200


@pytest.mark.parametrize(
    ("actor", "may_import"), [("preparer", False), ("b-admin", True)]
)
def test_statement_import_form_is_reserved_for_confirmed_admins(
    workspace, actor, may_import
):
    client, status = workspace
    status["members"].append(
        {
            "building_id": "preparer",
            "role": "member",
            "status": "confirmed",
            "access_roles": ["billing_preparer"],
        }
    )
    _set_session(client, actor)
    response = client.get(ROOT + "/billing")
    assert response.status_code == 200
    assert ('name="statement"' in response.text) is may_import


def test_billing_approval_error_distinguishes_unloaded_payments(workspace, monkeypatch):
    import billing_approval
    import dashboard

    client, _ = workspace
    _set_session(client, "b-admin")
    monkeypatch.setattr(
        dashboard.db,
        "list_bank_statement_entries",
        lambda _cid, *, reviewable, **_filters: (
            []
            if reviewable
            else [
                {
                    "id": 1,
                    "invoice_id": None,
                    "match_decision": "matched",
                    "confirmation_replay_available": False,
                    "booking_date": date(2026, 11, 19),
                    "imported_at": datetime(2026, 11, 20, tzinfo=UTC),
                    "payment_reference": "SYNTHETIC-PAID",
                    "amount": "10.00",
                    "currency": "CHF",
                    "credit_debit_indicator": "CRDT",
                    "source_name": "synthetic.xml",
                    "actor_id": "b-admin",
                }
            ]
        ),
    )

    def reject_approval(*_args):
        raise billing_approval.BillingApprovalError("synthetic refusal")

    monkeypatch.setattr(dashboard, "leg_approve_billing_period", reject_approval)
    assert "SYNTHETIC-PAID" in client.get(ROOT + "/billing").text
    response = client.post(
        ROOT + "/billing/period/1/approve",
        data={"csrf_token": "csrf-secret", "confirm_approval": "yes"},
    )
    assert response.status_code == 409
    assert "Diese Ansicht lädt keine Zahlungsdaten." in response.text
    assert "Noch keine Bankzahlungen abgeglichen." not in response.text


@pytest.mark.parametrize("release", [False, True], ids=["legacy", "release"])
def test_journal_row_shows_its_note(workspace, monkeypatch, release):
    import dashboard

    client, _ = workspace
    entry = {
        "id": 7,
        "direction": "out",
        "channel": "post",
        "counterparty": "Netzbetreiber",
        "subject": "Anmeldung LEG",
        "attachment_filename": None,
        "created_at": "2026-11-20",
    }
    journal = [
        {**entry, "notes": "Rückruf am Montag vereinbart"},
        {**entry, "id": 8, "subject": "Ohne Notiz", "notes": ""},
    ]
    monkeypatch.setattr(dashboard.db, "list_correspondence", lambda _cid: journal)
    monkeypatch.setitem(client.application.config, "SITE_RELEASE_ENABLED", release)
    _set_session(client, "reader")

    page = client.get(ROOT + "/correspondence").text
    rows = page.split("<tbody", 1)[1].split("</tbody>", 1)[0].split("<tr")[1:]
    noted = next(row for row in rows if "Anmeldung LEG" in row)
    assert "Rückruf am Montag vereinbart" in noted
    plain = next(row for row in rows if "Ohne Notiz" in row)
    assert "Notiz" not in plain.replace("Ohne Notiz", "")
    assert len(rows) == 2
