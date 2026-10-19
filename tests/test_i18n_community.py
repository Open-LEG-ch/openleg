# SPDX-License-Identifier: AGPL-3.0-or-later
"""Presentation language and explicit background-email locale regressions."""

from pathlib import Path

import pytest
from flask import Flask, render_template

from i18n import init_app
from tests import test_i18n

isolated_environment = test_i18n.isolated_environment

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app():
    app = Flask(__name__, template_folder=str(ROOT / "templates"))
    app.config.update(
        SECRET_KEY="i18n-community-tests",
        BABEL_TRANSLATION_DIRECTORIES=str(ROOT / "translations"),
    )
    init_app(app)
    return app


def render(app, template, lang="de-CH", **context):
    with app.test_request_context("/?lang=" + lang):
        return render_template(template, **context)


def test_utility_login_translates_html_and_javascript_errors(app):
    french = render(app, "utility/login.html", "fr-CH")
    german = render(app, "utility/login.html")
    assert "E-mail professionnel" in french
    assert "Envoyer le lien de connexion" in french
    assert '"Erreur de connexion."' in french
    assert "Geschäftliche E-Mail" in german
    assert "Login-Link senden" in german
    assert '"Fehler beim Login."' in german
    assert "fetch('/utility/login'" in french


def test_interest_counts_keep_privacy_threshold_in_both_languages(app):
    from flask_babel import force_locale

    from municipality import _public_interest_label

    with app.app_context(), force_locale("fr_CH"):
        assert _public_interest_label(0) == "0 personne intéressée"
        assert _public_interest_label(1) == "< 3 personnes intéressées"
        assert _public_interest_label(2, confirmed=True) == (
            "< 3 personnes ayant confirmé leur intérêt"
        )
        assert _public_interest_label(4) == "4 personnes intéressées"
        assert _public_interest_label(None) == "Indisponible"
    with app.app_context(), force_locale("de_CH"):
        assert _public_interest_label(0) == "0 Interessierte"
        assert _public_interest_label(2) == "< 3 Interessierte"
        assert _public_interest_label(4, confirmed=True) == "4 bestätigte Interessierte"


def test_profile_cache_keeps_metadata_isolated_between_languages(app, monkeypatch):
    from flask_babel import force_locale

    import municipality_profile

    stored = {}
    neutral = {
        "profile": {"name": "Vully", "bfs_number": 1},
        "h4_tariff": {"total_rp_kwh": 27.5, "year": 2026},
        "seo_title": "neutral source",
        "seo_description": "neutral source",
        "jsonld": {
            "@graph": [
                {
                    "@type": "BreadcrumbList",
                    "itemListElement": [{"name": "Gemeindeverzeichnis"}],
                }
            ]
        },
        "league_chips": [{"label": "Kanton VD", "rank": 2}],
    }
    monkeypatch.setattr(
        municipality_profile, "_build_profile_context", lambda *a, **k: neutral
    )
    monkeypatch.setattr(
        municipality_profile.cache, "cache_get", lambda key: stored.get(key)
    )
    monkeypatch.setattr(
        municipality_profile.cache,
        "cache_set",
        lambda key, value, **k: stored.update({key: value}),
    )
    with app.app_context(), force_locale("fr_CH"):
        french = municipality_profile.profile_context(1, site_url="https://example.ch")
    with app.app_context(), force_locale("de_CH"):
        german = municipality_profile.profile_context(1, site_url="https://example.ch")
    assert french["seo_title"] == (
        "Tarif électrique de Vully 2026 : 27.5 ct./kWh, solaire et CEL | OpenLEG"
    )
    assert german["seo_title"] == (
        "Stromtarif Vully 2026: 27.5 Rp/kWh, Solar und LEG | OpenLEG"
    )
    assert (
        french["jsonld"]["@graph"][0]["itemListElement"][0]["name"]
        == "Répertoire des communes"
    )
    assert (
        german["jsonld"]["@graph"][0]["itemListElement"][0]["name"]
        == "Gemeindeverzeichnis"
    )
    assert french["league_chips"][0]["label"] == "Canton VD"
    assert german["league_chips"][0]["label"] == "Kanton VD"
    assert neutral["seo_title"] == "neutral source"
    assert len(stored) == 1
