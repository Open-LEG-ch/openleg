# SPDX-License-Identifier: AGPL-3.0-or-later
"""Utility access mail and browser response use the chosen display language."""

from pathlib import Path

import pytest
from flask import Flask

from i18n import init_app
from tests import test_i18n

isolated_environment = test_i18n.isolated_environment

with test_i18n._synthetic_environment():
    import utility_portal


@pytest.fixture
def web():
    web = Flask(
        __name__, template_folder=str(Path(__file__).resolve().parents[1] / "templates")
    )
    web.config.update(
        SECRET_KEY="utility-language-test",
        APP_BASE_URL="https://example.ch",
        BABEL_TRANSLATION_DIRECTORIES=str(
            Path(__file__).resolve().parents[1] / "translations"
        ),
    )
    init_app(web)
    web.register_blueprint(utility_portal.utility_bp)
    return web


def test_french_utility_registration_validation_keeps_status_and_schema(web):
    response = web.test_client().post("/utility/register?lang=fr", json={})
    assert response.status_code == 400
    assert response.json == {
        "error": "Le nom de l’entreprise et l’adresse e-mail sont obligatoires."
    }


@pytest.mark.parametrize(
    "tier,german,french",
    [
        ("starter", "Einstieg", "Offre de base"),
        ("professional", "Professionell", "Professionnel"),
        ("enterprise", "Unternehmen", "Entreprise"),
    ],
)
@pytest.mark.parametrize("locale", ["de-CH", "fr-CH"])
def test_stored_utility_plan_names_and_zero_population_are_localized(
    web, monkeypatch, tier, german, french, locale
):
    from datetime import UTC, datetime

    client_data = {
        "client_id": "synthetic-client",
        "company_name": "Synthetic",
        "status": "active",
        "tier": tier,
        "population": 0,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "last_login_at": None,
    }
    monkeypatch.setattr(utility_portal.db, "get_utility_client", lambda *a: client_data)
    client = web.test_client()
    with client.session_transaction() as state:
        state["utility_client_id"] = "synthetic-client"
    response = client.get("/utility/dashboard?lang=" + locale)
    assert response.status_code == 200
    assert (french if locale == "fr-CH" else german) in response.text
    assert '<p class="text-2xl font-bold">0</p>' in response.text
    assert client_data["tier"] == tier
