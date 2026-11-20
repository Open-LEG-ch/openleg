# SPDX-License-Identifier: AGPL-3.0-or-later
"""Participation links and the public code map remain usable and evidence-backed."""

import re
from pathlib import Path

from tests import test_i18n

app_module = test_i18n.app_module
isolated_environment = test_i18n.isolated_environment
web = test_i18n.web


def test_community_dates_require_configured_evidence(web):
    web.config["SITE_RELEASE_ENABLED"] = True
    client = web.test_client()
    text = client.get("/mitmachen").text
    assert "1. November 2026" not in text
    assert "soll es tragen" in text
    web.config["VEREIN_FORMATION_DATE"] = "2026-01-01"
    text = client.get("/mitmachen").text
    assert "Der Verein openLEG Schweiz trägt es" in text
    assert "1. Januar 2026" in text
    assert "dreimonatiger Versuch" not in text
    assert "Einmal im Monat online" in text
    assert "Einmal im Quartal vor Ort" in text
    hrefs = set(re.findall(r'href="([^"]+)"', text))
    base = "https://github.com/Open-LEG-ch/openleg/"
    assert base + "issues/new?template=anfrage.yml" in hrefs
    for name in ("GOVERNANCE", "CONTRIBUTING", "MAINTAINERS", "CODE_OF_CONDUCT"):
        assert base + "blob/main/" + name + ".md" in hrefs
    assert "mailto:hallo@openleg.ch" in hrefs
    steps = text.split('class="stewardship-steps">', 1)[1].split("</ol>", 1)[0]
    assert steps.count("<li>") == 5
    assert "Kevin-Nexio" in text


def test_map_text_equivalent_matches_both_views_and_real_modules(web):
    web.config["SITE_RELEASE_ENABLED"] = True
    text = web.test_client().get("/software").text
    maps = re.findall(r'<svg viewBox="0 0 280 1160".*?</svg>', text, re.DOTALL)
    assert len(maps) == 2
    equivalent = text.split('class="code-map-equivalent"', 1)[1].split("</ol>", 1)[0]
    items = re.findall(
        r"<li><strong>(.*?)</strong>.*?<code>(.*?)</code></li>", equivalent
    )
    assert len(items) == 8
    root = Path(__file__).resolve().parents[1]
    for index, (label, modules) in enumerate(items):
        for diagram in maps:
            assert re.findall(r"<strong>(.*?)</strong>", diagram)[index] == label
        for module in modules.split(" · "):
            if module != "Flask":
                assert (root / module).exists(), module
    assert "keine zugesagte Antwortzeit" in text
    assert "standardmässig ausgeschaltet" in text
