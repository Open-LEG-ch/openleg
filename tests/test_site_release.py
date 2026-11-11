# SPDX-License-Identifier: AGPL-3.0-or-later
"""The complete site is opt-in; drafting does not invent a founded Verein."""

import re

import pytest

from tests import test_i18n

app_module = test_i18n.app_module
isolated_environment = test_i18n.isolated_environment
web = test_i18n.web


@pytest.mark.parametrize(
    "path",
    ["/software", "/wissen", "/dokumente", "/community", "/mitmachen", "/kontakt"],
)
def test_new_project_destinations_require_complete_site(web, path):
    client = web.test_client()
    assert client.get(path).status_code == 404
    web.config["SITE_RELEASE_ENABLED"] = True
    response = client.get(path)
    assert response.status_code == 200
    assert response.text.count("<h1") == 1
    assert 'href="/verein"' in response.text


def test_release_costs_do_not_offer_unapproved_hosting(web):
    web.config["SITE_RELEASE_ENABLED"] = True
    response = web.test_client().get("/pricing")
    assert response.status_code == 200
    assert "Was beim Betrieb Kosten verursacht." in response.text
    assert "CHF 0" not in response.text
    assert "Für immer" not in response.text
    assert "AGPL-3.0-or-later" in response.text


def test_verified_formation_metadata_updates_public_status_and_join_link(web):
    web.config.update(
        SITE_RELEASE_ENABLED=True,
        VEREIN_FORMATION_DATE="2026-01-01",
        VEREIN_STATUTES_URL="/statuten.pdf",
    )
    response = web.test_client().get("/verein")
    assert "noch nicht gegründet" not in response.text
    assert "Der Verein wurde am 1. Januar 2026 gegründet" in response.text
    assert "Sitz in Obersiggenthal" in response.text
    assert 'href="/verein/beitreten"' not in response.text
    assert 'href="mailto:hallo@openleg.ch"' in response.text
    assert 'href="/statuten.pdf"' in response.text
    web.config.update(
        VEREIN_ACCEPTING_APPLICATIONS=True,
        VEREIN_CONTACT_ADDRESS="Fictional contact",
        VEREIN_PRIVACY_VERSION="fictional-reviewed-v1",
        VEREIN_DATA_CONTROLLER="Fictional test association",
    )
    assert 'href="/verein/beitreten"' in web.test_client().get("/verein").text


def test_new_verein_surface_is_not_published_by_default(web):
    assert web.test_client().get("/verein").status_code == 404


def test_private_release_preview_explains_pending_formation(web):
    web.config["SITE_RELEASE_ENABLED"] = True
    response = web.test_client().get("/verein")
    assert response.status_code == 200
    assert "Verein openLEG Schweiz" in response.text
    assert "Obersiggenthal" in response.text
    assert "noch nicht gegründet" in response.text
    assert "20. Oktober 2026" not in response.text
    assert "CHF 50" in response.text
    assert 'name="email"' not in response.text


def test_release_shell_has_static_identity_and_task_navigation(web):
    web.config["SITE_RELEASE_ENABLED"] = True
    response = web.test_client().get("/verein")
    assert 'data-site-release="true"' in response.text
    assert "ol-caret" not in response.text
    assert 'href="/login"' in response.text
    assert 'href="/utility/login"' in response.text
    assert 'aria-controls="release-menu"' in response.text
    assert 'src="/static/js/site_navigation.js"' in response.text
    for label in ("LEG gründen", "Software", "Für Gemeinden", "Wissen", "Verein"):
        assert label in response.text


def test_release_home_explains_tasks_and_keeps_real_address_intake(web, monkeypatch):
    import database
    import homepage_view_model

    web.config["SITE_RELEASE_ENABLED"] = True
    monkeypatch.setattr(
        database, "get_stats", lambda city_id=None: {"total_buildings": 0}
    )
    monkeypatch.setattr(homepage_view_model, "ranking_extremes", lambda: ([], [], 0))
    client = web.test_client()
    response = client.get("/")
    assert response.status_code == 200
    assert response.text.count("<h1") == 1
    assert "Solarstrom im Quartier teilen. Eine LEG gründen." in response.text
    assert 'href="/leg-gruenden"' in response.text
    assert 'class="release-home"' in response.text
    assert 'viewBox="0 0 900 730"' in response.text
    for identifier in (
        "registrieren",
        "address-input",
        "address-status",
        "map",
        "step-address",
    ):
        assert response.text.count('id="' + identifier + '"') == 1
    assert "Fiktives Quartier" in response.text
    assert "noch nicht gegründet" in response.text
    with client.session_transaction() as session:
        session["dashboard_building_id"] = "existing-resident"
    assert client.get("/").headers["Location"] == "/dashboard"


def _release_home(web, monkeypatch):
    import database
    import homepage_view_model

    web.config["SITE_RELEASE_ENABLED"] = True
    monkeypatch.setattr(
        database, "get_stats", lambda city_id=None: {"total_buildings": 0}
    )
    monkeypatch.setattr(homepage_view_model, "ranking_extremes", lambda: ([], [], 0))
    return web.test_client()


def test_practice_village_is_not_offered_without_a_configured_service(web, monkeypatch):
    client = _release_home(web, monkeypatch)
    web.jinja_env.globals["sandbox_url"] = ""
    for path in ("/", "/software"):
        response = client.get(path)
        assert response.status_code == 200
        assert "release-practice" not in response.text
        assert "/sandbox" not in response.text
    assert 'href="/dashboard/demo"' in client.get("/").text


def test_practice_village_links_each_role_to_its_preselected_account(web, monkeypatch):
    from sandbox_seed import ACCOUNTS

    client = _release_home(web, monkeypatch)
    web.jinja_env.globals["sandbox_url"] = "https://sonnwil.example.org/sandbox"
    home = client.get("/").text
    assert 'href="https://sonnwil.example.org/sandbox"' in home
    assert 'class="release-practice"' not in home
    for path in ("/software",):
        text = client.get(path).text
        assert text.count('class="release-practice"') == 1
        assert 'href="https://sonnwil.example.org/sandbox"' in text
        roles = re.findall(r'sandbox\?konto=([a-z]+)#konto-\1"', text)
        assert roles == [
            "resident",
            "founder",
            "administrator",
            "municipality",
            "utility",
        ]
        assert set(roles) <= set(ACCOUNTS)
    french = client.get("/software?lang=fr-CH").text
    assert (
        'href="https://sonnwil.example.org/sandbox?konto=founder&amp;lang=fr-CH#konto-founder"'
        in french
    )
