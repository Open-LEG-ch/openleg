# SPDX-License-Identifier: AGPL-3.0-or-later
"""The association draft has its own scope and does not open membership intake."""

import pytest

from tests import test_i18n

app_module = test_i18n.app_module
isolated_environment = test_i18n.isolated_environment
web = test_i18n.web


@pytest.mark.parametrize("locale", ["de", "fr", "it", "en"])
def test_association_notice_has_stable_version_and_controller(web, locale):
    web.config["SITE_RELEASE_ENABLED"] = True
    client = web.test_client()
    response = client.get("/datenschutz?lang=" + locale)
    assert response.status_code == 200
    assert 'id="verein-datenschutz"' in response.text
    assert "verein-2026-11-13-draft-1" in response.text
    assert "Verein openLEG Schweiz" in response.text
    assert "Postfach, 5400 Baden" in response.text
    assert 'href="mailto:hallo@openleg.ch"' in response.text
    assert 'href="/verein/beitreten"' not in client.get("/verein").text


def test_notice_is_scoped_to_release_and_explicit_about_retention(web):
    client = web.test_client()
    assert 'id="verein-datenschutz"' not in client.get("/datenschutz").text
    web.config["SITE_RELEASE_ENABLED"] = True
    text = client.get("/datenschutz").text
    assert "Entwurf zur rechtlichen Prüfung" in text
    assert "keine automatische Bereinigung" in text
    assert "löscht keine Vereinsdaten" in text
    assert "die private Versionsgeschichte bleibt erhalten" in text


def test_application_links_to_notice_and_records_its_configured_version(
    web, monkeypatch
):
    import database
    import verein_routes

    submitted = []
    monkeypatch.setattr(
        database, "submit_verein_application", lambda **data: submitted.append(data)
    )
    monkeypatch.setattr(verein_routes.email_utils, "EMAIL_ENABLED", True)
    monkeypatch.setattr(
        verein_routes, "validate_email_address", lambda email: (True, email, None)
    )
    web.config.update(
        SITE_RELEASE_ENABLED=True,
        VEREIN_ACCEPTING_APPLICATIONS=True,
        VEREIN_FORMATION_DATE="2026-01-01",
        VEREIN_CONTACT_ADDRESS="Fictional contact address",
        VEREIN_DATA_CONTROLLER="Fictional test controller",
        VEREIN_STATUTES_URL="/statutes.pdf",
        VEREIN_PRIVACY_VERSION="fictional-reviewed-v1",
    )
    client = web.test_client()
    text = client.get("/verein/beitreten?lang=fr").text
    assert '#verein-datenschutz"' in text
    assert "lang=fr-CH#verein-datenschutz" in text

    notice = client.get("/datenschutz?lang=fr").text
    assert "Version : fictional-reviewed-v1" in notice
    with client.session_transaction() as session:
        csrf = session["verein_csrf"]
    result = client.post(
        "/verein/beitreten",
        data={
            "csrf_token": csrf,
            "name": "Example Member",
            "email": "member@example.org",
            "adult": "yes",
            "independent": "yes",
            "terms": "yes",
        },
    )
    assert result.status_code == 202
    assert submitted[0]["privacy_version"] == "fictional-reviewed-v1"
