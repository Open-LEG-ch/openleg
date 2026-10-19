# SPDX-License-Identifier: AGPL-3.0-or-later
"""Product flows translate their presentation while preserving domain values."""

import json
import re
from html import unescape
from unittest.mock import MagicMock

from tests import test_i18n

app_module = test_i18n.app_module
isolated_environment = test_i18n.isolated_environment
web = test_i18n.web

# Imported route seams also read configuration on a cold Python process.
with test_i18n._synthetic_environment():
    import app
    import community_access
    import dashboard
    import formation_wizard
    from tests.test_dashboard_access_routes import _set_session


def test_french_resident_demo_has_translated_checks_actions_and_accessibility(web):
    response = web.test_client().get("/dashboard/demo?lang=fr-CH")
    assert response.status_code == 200
    body = unescape(response.text)
    assert "Votre communauté électrique est prête à 75%." in body
    assert "Adresse e-mail confirmée" in body
    assert "Consentement à l’affichage aux voisins accordé" in body
    assert "Accorder le consentement" in body
    assert 'aria-label="Prêt à 75 pour cent"' in body
    assert 'toLocaleString("fr-CH")' in body
    assert "Mellingerstrasse 12, 5400 Baden" in body
    assert 'name="annual_consumption_kwh"' in body
    assert "EVU-Einwilligung erteilt" not in body


def test_german_demo_retains_original_checks_and_action_bindings(web):
    response = web.test_client().get("/dashboard/demo")
    assert response.status_code == 200
    body = response.text
    assert "Ihre Stromgemeinschaft ist zu 75% bereit." in body
    assert "Nachbar-Einwilligung erteilt" in body
    assert 'href="#einwilligungen"' in body
    assert "Einwilligung erteilen" in body


def test_french_formation_demo_does_not_expose_german_status_vocabulary(web):
    response = web.test_client().get("/leg/dashboard/demo?lang=fr")
    assert response.status_code == 200
    body = unescape(response.text)
    assert "Création démarrée" in body
    assert "Selon la consommation et la production" in body
    assert "Batterie de quartier" in body
    assert "LEG Musterweg" in body  # A stored community name is never translated.
    assert "Gründung gestartet" not in body


def test_french_member_invoice_list_preserves_invoice_identity_and_amount(
    web, monkeypatch
):
    lookup = MagicMock(
        return_value={
            "invoices": [
                {
                    "id": 42,
                    "invoice_number": "LEG-2026-001",
                    "period_label": "2026-09",
                    "status_label": "Freigegeben",
                    "display_gross_chf": "42.00",
                    "due_date": "2026-10-31",
                }
            ]
        }
    )
    monkeypatch.setattr(app.dashboard_module, "member_invoices_view", lookup)
    client = web.test_client()
    _set_session(client)
    response = client.get("/dashboard/invoices?lang=fr")
    assert response.status_code == 200
    body = unescape(response.text)
    assert "Mes factures" in body
    assert "Validée" in body
    assert "LEG-2026-001" in body
    assert "42.00 CHF" in body
    assert "2026-10-31" in body
    assert response.headers["Cache-Control"] == "no-store"
    lookup.assert_called_once_with("building-session")


def test_french_request_never_translates_domain_error_keys_or_role_values(web):
    with web.test_request_context("/?lang=fr"):
        assert dashboard.readiness(None)["error"] == "Kein Profil angegeben."
        assert formation_wizard.MEMBER_ROLE_LABELS == {
            "admin": "Verwaltung",
            "member": "Mitglied",
        }
        assert community_access.ADMIN == "admin"
        # These dictionaries may also be serialized or used by other consumers.
        assert (
            json.loads(json.dumps(formation_wizard.MEMBER_STATUS_LABELS))["confirmed"]
            == "Bestätigt"
        )


def test_french_javascript_copy_strings_are_json_serialized(web):
    response = web.test_client().get("/dashboard/demo?lang=fr")
    script = re.search(r"copyBtn.textContent = (.*?);", response.text)
    assert script is not None
    assert json.loads(script.group(1)) == "Copié"
