# SPDX-License-Identifier: AGPL-3.0-or-later
"""Address intake's server/browser contract, for both homepage themes."""

import json
import re

import pytest
from flask import render_template

from tests import test_i18n

app_module = test_i18n.app_module
isolated_environment = test_i18n.isolated_environment
web = test_i18n.web


@pytest.mark.parametrize("release", [False, True])
@pytest.mark.parametrize("locale", ["de-CH", "fr-CH", "it-CH", "en"])
def test_homepage_uses_shared_script_and_serialized_translations(
    web, monkeypatch, release, locale
):
    import database as db

    monkeypatch.setattr(db, "get_stats", lambda city_id=None: {"total_buildings": 0})
    import homepage_view_model

    monkeypatch.setattr(homepage_view_model, "ranking_extremes", lambda: ([], [], 0))
    web.config["SITE_RELEASE_ENABLED"] = release
    response = web.test_client().get("/?lang=" + locale)
    assert response.status_code == 200
    html = response.text
    assert html.count('src="/static/js/address_intake.js"') == 1
    assert "fetch('/api/check_potential'" not in html
    assert html.index('id="address-input"') < html.index('id="map"')
    config = json.loads(
        re.search(r"data-address-config>(.*?)</script>", html, re.DOTALL)[1]
    )
    expected_locale = locale if release or locale in ("de-CH", "fr-CH") else "de-CH"
    assert config["locale"] == expected_locale
    assert isinstance(config["map"]["center"], list)
    assert config["messages"]["networkRetry"]
    assert (
        config["messages"]["checkAddress"]
        == {
            "de-CH": "Adresse prüfen",
            "fr-CH": "Vérifier une adresse",
            "it-CH": "Controlla l' indirizzo",
            "en": "Check address",
        }[expected_locale]
    )
    assert 'role="status" aria-live="polite"' in html
    assert 'role="alert" aria-live="assertive"' in html


def test_partial_accepts_heading_lead_and_variant_with_safe_json(web):
    with web.test_request_context("/"):
        html = render_template(
            "partials/address_intake.html",
            user_count=0,
            address_heading="Local heading",
            address_lead="Local lead",
            address_variant="editorial",
            current_locale="en",
            map_center_lat="</script><script>bad()</script>",
            map_center_lon=8,
            map_zoom=10,
        )
    assert "Local heading" in html
    assert "Local lead" in html
    assert "address-intake--editorial" in html
    assert html.index('id="address-input"') < html.index('id="map"')
    config = json.loads(
        re.search(r"data-address-config>(.*?)</script>", html, re.DOTALL)[1]
    )
    assert config["map"]["center"][0] == "</script><script>bad()</script>"
    assert "</script><script>bad()" not in html
