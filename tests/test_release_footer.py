# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared footer destinations keep the request locale and optional sandbox."""

import html
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from tests import test_i18n

app_module = test_i18n.app_module
isolated_environment = test_i18n.isolated_environment
web = test_i18n.web


@pytest.mark.parametrize("path", ["/", "/wissen"])
@pytest.mark.parametrize(
    "locale,guide_label,demo_label",
    [
        ("de-CH", "LEG vorbereiten", "Demodorf öffnen"),
        ("en", "Prepare a LEG", "Open the demo village"),
        ("fr-CH", "Préparer une CEL", "Ouvrir la démo"),
        ("it-CH", "Preparare una LEG", "Aprire il villaggio demo"),
    ],
)
@pytest.mark.parametrize(
    "sandbox", ["", "https://village.example/sandbox?entry=footer#start"]
)
def test_shared_footer_next_actions(
    web, monkeypatch, path, locale, guide_label, demo_label, sandbox
):
    import database
    import homepage_view_model

    web.config["SITE_RELEASE_ENABLED"] = True
    monkeypatch.setitem(web.jinja_env.globals, "sandbox_url", sandbox)
    monkeypatch.setattr(
        database, "get_stats", lambda city_id=None: {"total_buildings": 0}
    )
    monkeypatch.setattr(homepage_view_model, "ranking_extremes", lambda: ([], [], 0))
    client = web.test_client()
    response = client.get(path, query_string={"lang": locale})
    assert response.status_code == 200
    footer = re.search(
        r'<footer class="release-footer">(.*?)</footer>', response.text, re.DOTALL
    ).group(1)
    links = {
        html.unescape(label): html.unescape(url)
        for url, label in re.findall(r'<a href="([^"]+)">([^<]+)</a>', footer)
    }
    guide = urlsplit(links[guide_label])
    assert guide.path == "/leg-gruenden"
    assert parse_qs(guide.query).get("lang", ["de-CH"]) == [locale]
    assert client.get(links[guide_label]).status_code == 200
    if sandbox:
        demo = urlsplit(links[demo_label])
        assert (demo.scheme, demo.netloc, demo.path, demo.fragment) == (
            "https",
            "village.example",
            "/sandbox",
            "start",
        )
        assert parse_qs(demo.query) == {
            "entry": ["footer"],
            "lang": [locale],
        }
    else:
        assert demo_label not in links
        assert "village.example" not in footer
        assert "/sandbox" not in footer

    destinations = {urlsplit(url).path for url in links.values()}
    assert {
        "/verein",
        "/community",
        "/mitmachen",
        "/kontakt",
        "/Open-LEG-ch/openleg",
        "/wissen",
        "/dokumente",
        "/how-it-works",
        "/rangliste",
        "/rangliste/methodik",
        "/self-host",
        "/api/v1/docs",
        "/login",
        "/utility/login",
        "/datenschutz",
        "/impressum",
        "/Open-LEG-ch/openleg/blob/main/LICENSE",
    } <= destinations
