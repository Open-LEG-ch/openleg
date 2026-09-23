# SPDX-License-Identifier: AGPL-3.0-or-later
"""HTTP contracts for the dashboard product entry point."""

import re

import pytest

from tests.test_app_organic_routes import _disable_rate_limit_hooks


@pytest.fixture
def app_module(monkeypatch):
    import app as imported_app

    web = imported_app.create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "root-role-access-test-key",
            "APP_BASE_URL": "http://localhost:5003",
            "RATELIMIT_STORAGE_URI": "memory://",
        },
        load_environment=False,
        check_database=False,
    )
    hooks = _disable_rate_limit_hooks(web)
    monkeypatch.setattr(
        imported_app.db,
        "get_stats",
        lambda city_id=None: {"total_buildings": 0},
    )
    try:
        imported_app.web = web
        yield imported_app
    finally:
        web.before_request_funcs[None] = hooks


def _hrefs(html):
    return re.findall(r'href="([^"]+)"', html)


def test_anonymous_root_renders_public_homepage_not_dashboard_access(app_module):
    response = app_module.web.test_client().get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Stromgemeinschaft" in html
    assert "Ihre Gemeinschaft." not in html
    assert "Was ist eine LEG?" in html
    assert "Dashboard-Zugang" not in html
    assert 'class="site-nav ' in html
    assert "<footer" in html


def test_login_offers_exactly_owner_and_municipality_access(app_module):
    response = app_module.web.test_client().get("/login")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Eigentümer" in html
    assert "Dashboard-Zugang" in html
    role_hrefs = [
        href for href in _hrefs(html) if href in {"/dashboard", "/gemeinde/dashboard"}
    ]
    assert role_hrefs.count("/dashboard") == 1
    assert role_hrefs.count("/gemeinde/dashboard") == 1
    assert len(role_hrefs) == 2


@pytest.mark.parametrize(
    "path",
    [
        "/how-it-works",
        "/fuer-bewohner",
        "/fuer-gemeinden",
        "/open-source",
        "/leg-gruenden",
        "/leg-kalkulator",
        "/pricing",
        "/impressum",
        "/datenschutz",
        "/self-host",
        "/rangliste",
        "/rangliste/methodik",
        "/robots.txt",
        "/sitemap.xml",
    ],
)
def test_restored_public_website_navigation_targets_render(app_module, path):
    response = app_module.web.test_client().get(path)

    assert response.status_code == 200


def test_leg_guide_puts_common_requirements_before_dated_vnb_processes(app_module):
    response = app_module.web.test_client().get("/leg-gruenden")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    common_requirements = html.index("Was für jede LEG gilt")
    operator_processes = html.index("So unterscheiden sich die Netzbetreiber")
    assert common_requirements < operator_processes
    assert "gleichen Gemeinde" in html[common_requirements:operator_processes]
    assert "gleichen Netzgebiet" in html[common_requirements:operator_processes]
    assert "zulässigen Netzebenen" in html[common_requirements:operator_processes]
    assert "mindestens 5 Prozent" in html[common_requirements:operator_processes]
    assert "Stand: 11. September 2026" in html[operator_processes:]


def test_leg_guide_compares_the_three_vnb_onboarding_processes(app_module):
    response = app_module.web.test_client().get("/leg-gruenden")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    expected_links = {
        "BKW": "https://www.bkw.ch/de/strom-in-der-grundversorgung/eigenen-strom-teilen-und-verkaufen/strom-mit-nachbarn-teilen/lokale-elektrizitaetsgemeinschaft",
        "Primeo Energie": "https://www.primeo-energie.ch/geschaeftskunden/photovoltaik/energiegemeinschaften/leg.html",
        "EBL": "https://www.ebl.ch/de/strom/eigenverbrauch-lokale-elektrizitaetsgemeinschaft",
    }
    articles = {}
    for operator, url in expected_links.items():
        article = re.search(
            rf"<article\b[^>]*>\s*<h3\b[^>]*>{operator}</h3>(.*?)</article>",
            html,
            flags=re.DOTALL,
        )
        assert article
        articles[operator] = article.group(1)
        assert f'href="{url}"' in articles[operator]

    for phrase in (
        "Messpunktnummern",
        "LEG-Portal",
        "Smart Meter der BKW",
        "Rohdaten im ebIX-Format",
        "selbst führen oder einen externen Dienstleister beauftragen",
    ):
        assert phrase in articles["BKW"]
    for phrase in (
        "myPrimeo-Kundenportal",
        "verbindliche Topologieauskunft",
        "Fehlende Smart Meter",
        "LEG-Vertretung rechnet",
    ):
        assert phrase in articles["Primeo Energie"]
    for phrase in (
        "Adresseingabe",
        "technischen Prüfung",
        "Ein- und Austritte online",
        "fehlende Smart Meter",
        "CSV oder S-DAT",
        "keine interne Abrechnung",
    ):
        assert phrase in articles["EBL"]


def test_leg_guide_states_the_vnb_boundary_in_a_mobile_readable_section(app_module):
    response = app_module.web.test_client().get("/leg-gruenden")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    comparison = re.search(
        r'<section\b[^>]*aria-labelledby="vnb-processes-title"[^>]*>(.*?)</section>',
        html,
        flags=re.DOTALL,
    )
    assert comparison
    section = comparison.group(1)
    assert section.count("<article") == 3
    assert "<table" not in section
    assert section.count("<a ") == 3
    assert 'tabindex="-1"' not in section
    assert "Die Abläufe der Netzbetreiber können sich ändern." in section
    assert "ohne Bindung an einen bestimmten VNB konzipiert" in section
    assert "Noch ist nicht jede VNB-Anbindung fertig umgesetzt." in section
    assert "Anmeldung und Datenlieferung müssen pro VNB konfiguriert werden." in section
    assert "OpenLEG funktioniert mit jedem Schweizer Netzbetreiber" not in html


def test_owner_session_redirects_root_to_owner_dashboard(app_module):
    client = app_module.web.test_client()
    with client.session_transaction() as state:
        state["dashboard_building_id"] = "building-session"

    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


def test_municipality_session_redirects_root_to_municipality_dashboard(app_module):
    client = app_module.web.test_client()
    with client.session_transaction() as state:
        state["municipality_id"] = 7

    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/gemeinde/dashboard")
