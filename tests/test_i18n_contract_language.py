# SPDX-License-Identifier: AGPL-3.0-or-later
"""Resource language remains tied to the contract content during UI translation."""

import pytest

import formation_wizard
from tests import test_i18n

isolated_environment = test_i18n.isolated_environment
app_module = test_i18n.app_module
web = test_i18n.web


@pytest.mark.parametrize("language", ["de-CH", "fr-CH"])
def test_template_language_describes_the_document_content(client, language):
    response = client.get(f"/api/v1/leg/templates?lang={language}")
    assert response.status_code == 200
    contracts = {item["name"]: item for item in response.get_json()["contracts"]}
    sources = formation_wizard.get_contract_templates()
    assert set(contracts) == set(sources)
    for name, source in sources.items():
        assert contracts[name]["language"] == source.get("language", "de")
        assert contracts[name]["sections"] == source["sections"]


def test_french_unsubscribe_explains_entry_scope_and_retained_records(web):
    response = web.test_client().get("/unsubscribe?lang=fr-CH")
    assert response.status_code == 200
    assert response.headers["Content-Language"] == "fr-CH"
    assert "profil d’adresse" in response.text
    assert "demande de couverture ouverte" in response.text
    assert "Les factures déjà émises" in response.text
    assert "les événements d’utilisation sont conservés" in response.text
    assert "bleiben erhalten" not in response.text
