# SPDX-License-Identifier: AGPL-3.0-or-later
"""Public-route contracts for the LEG formation guide."""

import json
import re
from html import unescape

import pytest

from tests.test_app_organic_routes import _disable_rate_limit_hooks


@pytest.fixture
def formation_client(monkeypatch):
    for name, value in {
        "DATABASE_URL": "postgresql://x:x@localhost/x",
        "REDIS_URL": "memory://",
        "CRON_SECRET": "test-cron-secret",
        "APP_BASE_URL": "http://localhost:5003",
        "PUBLIC_SITE_URL": "https://openleg.ch",
        "SESSION_COOKIE_SECURE": "false",
        "ALLOWED_HOSTS": "localhost",
        "SECRET_KEY": "formation-guide-test-key",
        "ADMIN_EMAIL": "admin@example.ch",
        "SESSION_COOKIE_SAMESITE": "Lax",
        "PERMANENT_SESSION_LIFETIME": "3600",
        "DASHBOARD_ACCESS_TOKEN_TTL_SECONDS": "900",
        "DASHBOARD_EMAIL_TOKEN_TTL_SECONDS": "86400",
    }.items():
        monkeypatch.setenv(name, value)
    import app as app_module

    application = app_module.create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "formation-guide-test-key",
            "APP_BASE_URL": "http://localhost:5003",
            "RATELIMIT_STORAGE_URI": "memory://",
        },
        load_environment=False,
        check_database=False,
    )
    hooks = _disable_rate_limit_hooks(application)
    try:
        yield application.test_client()
    finally:
        application.before_request_funcs[None] = hooks


def _formation_page(formation_client):
    response = formation_client.get("/leg-gruenden")

    assert response.status_code == 200
    return response.get_data(as_text=True)


def _formation_stages(html):
    return re.findall(
        r'<li id="schritt-(\d)" data-formation-stage="\1".*?</li>',
        html,
        flags=re.DOTALL,
    )


def _plain_text(fragment):
    return unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def _faq_block(html, question):
    return next(
        block
        for block in re.findall(
            r"<details data-faq-item.*?</details>", html, flags=re.DOTALL
        )
        if question in block
    )


def test_formation_guide_explains_the_local_eligibility_boundary(formation_client):
    html = _formation_page(formation_client)

    assert "politischen Gemeinde" in html
    assert "Netzgebiet desselben Verteilnetzbetreibers" in html
    assert "separate lokale LEGs" in html
    assert "5 Prozent" in html
    assert "Netzebene" in html
    assert "Topologieentscheid des Verteilnetzbetreibers ist verbindlich" in html


def test_formation_guide_answers_the_smart_meter_question(formation_client):
    html = _formation_page(formation_client)

    assert "vorhandene gesetzeskonforme Smart Meter des Verteilnetzbetreibers" in html
    assert "keinen separaten LEG-Zähler kaufen" in html
    assert "fehlende erforderliche Smart Meter" in html
    assert "gültigen Antrag" in html
    assert "regulierten Messtarife" in html


def test_formation_guide_keeps_six_ordered_stages_and_covers_the_full_workflow(
    formation_client,
):
    html = _formation_page(formation_client)

    assert _formation_stages(html) == ["1", "2", "3", "4", "5", "6"]
    stage_text = {
        number: match.group(1)
        for number in range(1, 7)
        if (
            match := re.search(
                rf'<li id="schritt-{number}".*?>(.*?)</li>', html, flags=re.DOTALL
            )
        )
    }
    assert "Adressen" in stage_text[1]
    assert "Verteilnetzbetreiber" in stage_text[1]
    assert "Teilnehmenden" in stage_text[2]
    assert "Anlagen" in stage_text[2]
    assert "Vertretung" in stage_text[3]
    assert "Vereinbarung" in stage_text[3]
    assert "drei Monate im Voraus auf ein Monatsende" in stage_text[5]
    assert "Messdaten" in stage_text[6]
    assert "interne Abrechnung" in stage_text[6]


def test_formation_guide_separates_vnb_invoices_from_internal_settlement(
    formation_client,
):
    html = _formation_page(formation_client)

    assert "misst die einzelnen Anschlüsse" in html
    assert "Anteile von LEG-Strom und Netzstrom" in html
    assert "Er stellt Netznutzung und Messung in Rechnung" in html
    assert "Grundversorgung" in html
    assert (
        "Die LEG rechnet den innerhalb der Gemeinschaft ausgetauschten Strom ab" in html
    )
    assert "Dienstleister" in html


def test_visible_faq_matches_faqpage_structured_data(formation_client):
    html = _formation_page(formation_client)
    visible_faq = [
        (_plain_text(question), _plain_text(answer))
        for question, answer in re.findall(
            r"<details data-faq-item.*?<summary[^>]*>(.*?)</summary>.*?"
            r"<p data-faq-answer[^>]*>(.*?)</p>",
            html,
            flags=re.DOTALL,
        )
    ]
    json_documents = [
        json.loads(document)
        for document in re.findall(
            r'<script type="application/ld\+json">(.*?)</script>',
            html,
            flags=re.DOTALL,
        )
    ]
    faq_page = next(
        document for document in json_documents if document.get("@type") == "FAQPage"
    )
    structured_faq = [
        (entry["name"], entry["acceptedAnswer"]["text"])
        for entry in faq_page["mainEntity"]
    ]

    assert visible_faq == structured_faq
    assert any("Smart Meter" in question for question, _answer in visible_faq)
    assert any("mehrere Gemeinden" in question for question, _answer in visible_faq)
    assert any("Abrechnung" in question for question, _answer in visible_faq)


def test_formation_guide_keeps_existing_next_step_and_source_links(formation_client):
    html = _formation_page(formation_client)

    for href in (
        "/leg-check",
        "/leg/dashboard/demo",
        "/pricing",
        "https://www.fedlex.admin.ch/eli/cc/2007/418/de",
        "https://www.fedlex.admin.ch/eli/cc/2008/226/de",
    ):
        assert f'href="{href}"' in html


def test_formation_faq_explains_agpl_installation_and_forks(formation_client):
    html = _formation_page(formation_client)
    faq = _faq_block(html, "Darf ich OpenLEG installieren, anpassen oder forken?")

    assert "AGPL-3.0-or-later" in faq
    assert "installieren, nutzen und anpassen" in faq
    assert "Fork ist erlaubt" in faq
    assert "veränderte Version über ein Netzwerk" in faq
    assert "entsprechenden Quellcode zugänglich machen" in faq
    assert "praktische Zusammenfassung und keine Rechtsberatung" in faq
    for href in (
        "https://github.com/Open-LEG-ch/openleg/blob/main/LICENSE",
        "https://github.com/Open-LEG-ch/openleg",
        "/self-host",
    ):
        assert f'href="{href}"' in faq
    assert "MIT-Lizenz" not in html


def test_formation_faq_explains_vnb_independence_without_overclaiming(
    formation_client,
):
    html = _formation_page(formation_client)
    faq = _faq_block(html, "Ist OpenLEG an einen bestimmten Netzbetreiber gebunden?")

    assert "ohne Bindung an einen bestimmten VNB konzipiert" in faq
    assert "Noch ist nicht jede VNB-Anbindung fertig umgesetzt" in faq
    assert "Anmeldung und Datenlieferung müssen pro VNB konfiguriert werden" in faq


def test_formation_faq_offers_paid_support_at_the_tenant_contact_address(
    formation_client, monkeypatch
):
    import app as app_module

    tenant = {
        **app_module.tenant_module.DEFAULT_TENANT,
        "contact_email": "beratung@example.ch",
    }
    monkeypatch.setattr(
        app_module.tenant_module,
        "get_tenant_config",
        lambda _territory, db=None: tenant,
    )

    html = _formation_page(formation_client)
    faq = _faq_block(html, "Kann OpenLEG unsere Gruppe persönlich unterstützen?")

    assert "Vorträge, Workshops und Projektunterstützung" in faq
    assert "nach vorgängiger Vereinbarung gegen Honorar" in faq
    assert 'href="mailto:beratung@example.ch"' in faq
    assert ">beratung@example.ch</a>" in faq
