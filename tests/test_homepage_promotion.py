# SPDX-License-Identifier: AGPL-3.0-or-later
"""The release homepage is available without experiment configuration."""

import pytest

from tests import test_i18n
from tests.test_site_release import _release_home

app_module = test_i18n.app_module
isolated_environment = test_i18n.isolated_environment
web = test_i18n.web


@pytest.mark.parametrize("locale", ["de-CH", "fr-CH", "en", "it-CH"])
@pytest.mark.parametrize("sandbox", ["", "https://sonnwil.example.org/sandbox"])
def test_default_homepage_uses_editorial_presentation(
    web, monkeypatch, locale, sandbox
):
    client = _release_home(web, monkeypatch)
    web.jinja_env.globals["sandbox_url"] = sandbox
    page = client.get("/?lang=" + locale)
    assert page.status_code == 200
    assert 'class="release-home"' in page.text
    assert page.text.count("<h1") == 1
    assert "/static/css/homepage.css" in page.text
    assert "_prototype" not in page.text
    assert page.text.count('id="address-input"') == 1
    assert (
        ('href="' + sandbox) in page.text
        if sandbox
        else 'href="/dashboard/demo' in page.text
    )


def test_homepage_uses_verified_identity_and_shared_facts(web, monkeypatch):
    from dataclasses import replace

    import verein_facts
    from release_identity import ReleaseIdentity

    client = _release_home(web, monkeypatch)
    web.config.update(
        VEREIN_FORMATION_DATE="2026-01-02",
        RELEASE_IDENTITY=ReleaseIdentity("v1.2.3", "a" * 40, "sha256:" + "b" * 64),
    )
    monkeypatch.setattr(
        verein_facts,
        "POLICY",
        replace(verein_facts.POLICY, legal_name="Test association", seat="Test seat"),
    )
    page = client.get("/").text
    hero = page.split("</section>", 1)[0]
    assert "https://github.com/Open-LEG-ch/openleg/tree/" + "a" * 40 in hero
    assert "v1.2.3" in hero
    assert "Test association" in page and "Test seat" in page
    assert "2. Januar 2026" in page
    assert "1. November 2026" not in page
    web.config["RELEASE_IDENTITY"] = None
    hero = client.get("/").text.split("</section>", 1)[0]
    assert "v1.2.3" not in hero and "/releases/tag/" not in hero


@pytest.mark.parametrize(
    "locale, rejected, draft, correction, responsibility",
    [
        ("de-CH", "Abgelehnt", "August 2026", "korrigiert", "berechtigte Person"),
        ("fr-CH", "Rejeté", "août 2026", "corrigées", "personne autorisée"),
        ("en", "Rejected", "August 2026", "corrected", "authorized person"),
        ("it-CH", "Respinto", "agosto 2026", "corretti", "persona autorizzata"),
    ],
)
def test_homepage_explains_correction_before_static_draft(
    web, monkeypatch, locale, rejected, draft, correction, responsibility
):
    from html.parser import HTMLParser

    class Elements(HTMLParser):
        def __init__(self, html):
            super().__init__()
            self.tags = []
            self.text = []
            self.feed(html)

        def handle_starttag(self, tag, attrs):
            self.tags.append((tag, dict(attrs)))

        def handle_data(self, data):
            self.text.append(data)

    client = _release_home(web, monkeypatch)
    html = client.get("/?lang=" + locale).text
    practice = html.split('<section class="home-practice"', 1)[1].split(
        "</section>", 1
    )[0]
    elements = Elements(practice)
    text = " ".join(elements.text).lower()
    rejected, draft, correction, responsibility = (
        value.lower() for value in (rejected, draft, correction, responsibility)
    )
    assert rejected in text
    assert text.index(rejected) < text.index(correction) < text.index(draft)
    assert responsibility in text
    assert not any(
        tag in {"input", "button", "form", "img"} for tag, _ in elements.tags
    )
    assert 'class="home-prepare-art"' not in html
    assert 'id="home-prepare-title"' in html
    assert 'class="home-steps"' in html


def test_italian_homepage_uses_consistent_community_and_draft_terms(web, monkeypatch):
    import re
    from html import unescape

    client = _release_home(web, monkeypatch)
    html = client.get("/?lang=it-CH").text
    text = unescape(re.sub("<[^>]+>", " ", html))
    assert "CLE" not in text
    practice = html.split('<section class="home-practice"', 1)[1].split(
        "</section>", 1
    )[0]
    assert "Progetto" not in practice
    assert "comunità locale di energia elettrica (LEG)" in text
    assert "bozza" in text.lower()
