# SPDX-License-Identifier: AGPL-3.0-or-later
"""Financial and LEG presentation regressions without external services."""

from copy import deepcopy
from html import unescape
from unittest.mock import MagicMock

import pytest
from flask import render_template

from i18n import gettext as _
from tests import test_i18n

app_module = test_i18n.app_module
isolated_environment = test_i18n.isolated_environment
web = test_i18n.web

# Imported route seams also read configuration on a cold Python process.
with test_i18n._synthetic_environment():
    import billing_policy
    import billing_workspace
    import community_access
    import dashboard
    import database as db
    import quartierakku
    from tests.test_dashboard_access_routes import _set_session


def _workspace():
    return {"error": None, "community_id": "community-a", "periods": [], "invoices": []}


def _policy():
    return {
        "effective_from": "2026-01-01",
        "internal_price_chf_per_kwh": "0.150000",
        "grid_fee_chf_per_kwh": "0.080000",
        "settlement_fee_chf_per_kwh": "0.020000",
        "network_level": "same",
        "distribution_model": "proportional",
        "vat_mode": "none",
        "payment_days": 30,
        "invoice_prefix": "SYNTHETIC",
        "delivery_method": "download",
    }


@pytest.mark.parametrize("lang", ["de-CH", "fr-CH"])
def test_admin_billing_translates_dynamic_evidence_without_changing_numbers(
    web, monkeypatch, lang
):
    period = {
        "id": 1,
        "community_id": "community-a",
        "period_start": "2026-08-31T22:00:00+00:00",
        "status": "draft",
        "distribution_model": "proportional",
        "network_level": "same",
        "internal_price_chf_per_kwh": "0.150000",
        "grid_fee_chf_per_kwh": "0.080000",
        "reconciliation": {"difference_kwh": 0},
        "line_items": [],
    }
    original = deepcopy(period)
    monkeypatch.setattr(db, "list_billing_periods", lambda **kwargs: [period])
    monkeypatch.setattr(db, "get_billing_period", lambda period_id: period)
    model = billing_workspace.load()
    with web.test_request_context("/admin/abrechnungen?lang=" + lang):
        body = unescape(render_template("admin/abrechnungen.html", **model))
    assert period == original
    assert model["selected"]["status"] == "draft"
    if lang == "fr-CH":
        for expected in (
            "septembre 2026",
            "Brouillon",
            "Ce projet de décompte n’est pas une facture définitive.",
            "Entièrement rapproché",
            "15.00 ct./kWh",
            "8.00 ct./kWh",
        ):
            assert expected in body
        assert "September 2026" not in body
        assert "Rp./kWh" not in body
    else:
        assert "September 2026" in body
        assert "Dieser Abrechnungsentwurf ist keine definitive Rechnung." in body
        assert "Vollständig abgeglichen" in body
        assert "15.00 Rp./kWh" in body


def test_policy_history_translates_days_and_rates_at_rendering_boundary(
    web, monkeypatch
):
    policy = _policy()
    original = deepcopy(policy)
    monkeypatch.setattr(dashboard, "_require_capability", lambda *args: True)
    monkeypatch.setattr(dashboard, "_internal_price_band", lambda *args: None)
    monkeypatch.setattr(db, "list_billing_policies", lambda *args: [policy])
    with web.test_request_context("/?lang=fr-CH"):
        model = dashboard.leg_billing_policy_view("community-a", "member-a")
        body = unescape(render_template("leg_billing_policy.html", **model))
    assert policy == original
    assert model["policy_versions"][0]["payment_days_display"] == "30 Tage"
    assert "30 jours" in body
    for amount in ("15.00", "8.00", "2.00"):
        assert amount + " ct./kWh" in body
    assert "Rp./kWh" not in body
    assert 'value="same"' in body and 'value="download"' in body


@pytest.mark.parametrize(
    "legacy_fields", [{}, {"payment_days": None, "invoice_prefix": None}]
)
def test_legacy_policy_history_localizes_missing_fields(
    web, monkeypatch, legacy_fields
):
    policy = _policy()
    del policy["payment_days"]
    del policy["invoice_prefix"]
    policy.update(legacy_fields)
    original = deepcopy(policy)
    monkeypatch.setattr(dashboard, "_require_capability", lambda *args: True)
    monkeypatch.setattr(dashboard, "_internal_price_band", lambda *args: None)
    monkeypatch.setattr(db, "list_billing_policies", lambda *args: [policy])
    with web.test_request_context("/?lang=fr-CH"):
        model = dashboard.leg_billing_policy_view("community-a", "member-a")
        body = unescape(render_template("leg_billing_policy.html", **model))
    assert policy == original
    assert "Nicht angegeben" not in body
    assert body.count("Non renseigné") >= 2


def test_invalid_policy_days_are_translated_without_changing_domain_error(
    web, monkeypatch
):
    errors = billing_policy.validate_policy_form({"payment_days": "0"})["errors"]
    original = deepcopy(errors)
    monkeypatch.setattr(
        dashboard,
        "leg_save_billing_policy",
        lambda *args: {"error": None, "errors": errors},
    )
    monkeypatch.setattr(dashboard, "_require_capability", lambda *args: True)
    monkeypatch.setattr(dashboard, "_internal_price_band", lambda *args: None)
    monkeypatch.setattr(db, "list_billing_policies", lambda *args: [])
    client = web.test_client()
    _set_session(client)
    response = client.post(
        "/leg/community/community-a/billing-policy?lang=fr-CH",
        data={"csrf_token": "csrf-secret", "payment_days": "0"},
    )
    assert response.status_code == 400
    assert errors == original
    assert (
        errors["payment_days"] == "Zahlungsfrist in ganzen Tagen, zwischen 1 und 365."
    )
    assert "Délai de paiement en jours entiers, entre 1 et 365." in unescape(
        response.text
    )


@pytest.mark.parametrize("lang", ["de-CH", "fr-CH"])
def test_approval_conflict_is_localized_through_real_route(web, monkeypatch, lang):
    monkeypatch.setattr(
        dashboard,
        "leg_approve_billing_period",
        MagicMock(side_effect=db.BillingApprovalError()),
    )
    monkeypatch.setattr(
        dashboard, "leg_billing_workspace_view", lambda *args: _workspace()
    )
    client = web.test_client()
    _set_session(client)
    response = client.post(
        "/leg/community/community-a/billing/period/1/approve?lang=" + lang,
        data={"csrf_token": "csrf-secret", "confirm_approval": "yes"},
    )
    assert response.status_code == 409
    expected = (
        "Ce projet de décompte n’a pas pu être validé."
        if lang == "fr-CH"
        else "Dieser Abrechnungsentwurf konnte nicht freigegeben werden."
    )
    assert expected in unescape(response.text)


@pytest.mark.parametrize("lang", ["de-CH", "fr-CH"])
def test_invoice_lifecycle_conflict_is_localized_through_real_route(
    web, monkeypatch, lang
):
    monkeypatch.setattr(
        dashboard,
        "leg_cancel_invoice",
        MagicMock(side_effect=dashboard.InvoiceLifecycleError()),
    )
    monkeypatch.setattr(
        dashboard, "leg_billing_workspace_view", lambda *args: _workspace()
    )
    client = web.test_client()
    _set_session(client)
    response = client.post(
        "/leg/community/community-a/billing/invoice/1/cancel?lang=" + lang,
        data={"csrf_token": "csrf-secret", "reason": "Synthetic correction"},
    )
    assert response.status_code == 409
    expected = (
        "Cette action n’est pas autorisée pour le statut actuel de la facture."
        if lang == "fr-CH"
        else "Die Aktion ist für den aktuellen Rechnungsstatus nicht zulässig."
    )
    assert expected in unescape(response.text)


def test_battery_errors_translate_the_participant_without_trusting_its_markup(
    web, monkeypatch
):
    participant = "member<script>"
    result = {
        "error": None,
        **quartierakku.validate_battery_form(
            {
                "participant_id": "battery-a",
                "capacity_kwh": "45",
                "annual_cost_chf": "960",
                "share:" + participant: "bad",
            },
            [participant],
        ),
    }
    original = deepcopy(result)
    monkeypatch.setattr(dashboard, "leg_save_battery", lambda *args: result)
    monkeypatch.setattr(
        dashboard, "leg_overview", lambda *args: dashboard.leg_demo_overview()
    )
    client = web.test_client()
    _set_session(client)
    response = client.post(
        "/leg/community/community-a/battery?lang=fr-CH",
        data={"csrf_token": "csrf-secret"},
    )
    assert response.status_code == 400
    assert result == original
    assert "Part de member&lt;script&gt; en pour cent, entre 0 et 100" in response.text
    assert "member<script>" not in response.text


def test_invalid_role_is_localized_at_route_boundary_and_keeps_canonical_error(
    web, monkeypatch
):
    invalid_role = "owner<script>"
    with pytest.raises(ValueError) as caught:
        community_access.validate_roles([invalid_role])
    original = str(caught.value)
    monkeypatch.setattr(
        dashboard, "leg_set_member_roles", lambda *args: {"error": original}
    )
    monkeypatch.setattr(
        dashboard, "leg_overview", lambda *args: dashboard.leg_demo_overview()
    )
    client = web.test_client()
    _set_session(client)
    response = client.post(
        "/leg/community/community-a/members/member-a/roles?lang=fr-CH",
        data={"csrf_token": "csrf-secret", "roles": invalid_role},
    )
    assert response.status_code == 400
    assert original == "Unbekannte LEG-Rolle: owner<script>"
    assert "Rôle CEL inconnu : owner&lt;script&gt;" in response.text
    assert "owner<script>" not in response.text


def test_leg_readiness_uses_a_complete_french_sentence_and_capitalized_heading(
    web, monkeypatch
):
    import formation_wizard

    monkeypatch.setitem(formation_wizard.FORMATION_CONFIG, "min_community_size", 2)
    response = web.test_client().get("/leg/dashboard/demo?lang=fr-CH")
    assert response.status_code == 200
    body = unescape(response.text)
    assert (
        "Au moins 2 membres confirment leur participation (30 points de pourcentage)."
        in body
    )
    assert ">Correspondance</h2>" in body


def test_dashboard_errors_are_translatable_without_mutating_domain_results(
    web, monkeypatch
):
    monkeypatch.setattr(db, "open_invoice_query", lambda *args: None)
    monkeypatch.setattr(dashboard, "_require_capability", lambda *args: True)
    monkeypatch.setattr(
        dashboard.formation_wizard, "create_community", lambda *args, **kwargs: None
    )
    with web.test_request_context("/?lang=fr-CH"):
        missing = dashboard.member_open_invoice_query(
            1, "member-a", "amount", "Question"
        )
        reply = dashboard.operator_update_invoice_query("community-a", "member-a", 1)
        creation = dashboard.leg_create("Synthetic", "member-a", "proportional")
        assert missing["error"] == "Rechnung nicht gefunden."
        assert reply["error"] == "Antwort oder Status ist erforderlich."
        assert creation["error"] == "LEG konnte nicht erstellt werden."
        assert _(missing["error"]) == "Facture introuvable."
        assert _(reply["error"]) == "Une réponse ou un statut est requis."
        assert _(creation["error"]) == "La CEL n’a pas pu être créée."
