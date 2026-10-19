# SPDX-License-Identifier: AGPL-3.0-or-later
"""HTTP failures keep their contracts and show safe localized explanations."""

import pytest
from flask import abort

from tests import test_i18n

isolated_environment = test_i18n.isolated_environment
app_module = test_i18n.app_module
web = test_i18n.web


@pytest.mark.parametrize(
    "status,german,french",
    [
        (400, "Ungültige Anfrage", "Requête non valide"),
        (401, "Anmeldung erforderlich", "Connexion requise"),
        (403, "Zugriff verweigert", "Accès refusé"),
        (404, "Seite nicht gefunden", "Page introuvable"),
        (409, "Aktion derzeit nicht möglich", "Action impossible dans l’état actuel"),
        (
            503,
            "Dienst vorübergehend nicht verfügbar",
            "Service temporairement indisponible",
        ),
    ],
)
@pytest.mark.parametrize("locale", ["de-CH", "fr-CH"])
def test_http_errors_are_localized_without_exposing_the_exception(
    web, status, german, french, locale
):
    @web.get("/test-error")
    def fail():
        abort(status, description="private@example.invalid <script>private</script>")

    response = web.test_client().get("/test-error?lang=" + locale)
    assert response.status_code == status
    assert response.headers["Content-Language"] == locale
    assert (french if locale == "fr-CH" else german) in response.text
    assert "private@example.invalid" not in response.text
    assert "<script>private</script>" not in response.text


def test_method_not_allowed_retains_the_allow_header(web):
    response = web.test_client().delete("/login?lang=fr-CH")
    assert response.status_code == 405
    assert "GET" in response.headers["Allow"]
    assert "Méthode non autorisée" in response.text


@pytest.mark.parametrize("locale", ["de-CH", "fr-CH"])
def test_private_invoice_denial_keeps_no_store_and_authorization(web, locale):
    response = web.test_client().get("/dashboard/invoices/999?lang=" + locale)
    assert response.status_code == 401
    assert "no-store" in response.headers["Cache-Control"]
    expected = "Connexion requise" if locale == "fr-CH" else "Anmeldung erforderlich"
    assert expected in response.text
