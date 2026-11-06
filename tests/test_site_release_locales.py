# SPDX-License-Identifier: AGPL-3.0-or-later
"""Extended presentation remains opt-in and shares the existing URL contract."""

import pytest

from tests import test_i18n

app_module = test_i18n.app_module
isolated_environment = test_i18n.isolated_environment
web = test_i18n.web


@pytest.mark.parametrize(
    "choice,language", [("de", "de-CH"), ("en", "en"), ("fr", "fr-CH"), ("it", "it-CH")]
)
def test_release_supports_four_explicit_choices_without_moving_private_urls(
    web, choice, language
):
    web.config["SITE_RELEASE_ENABLED"] = True
    client = web.test_client()
    response = client.get("/verein/mitglied/zugang?lang=" + choice)
    assert response.status_code == 200
    assert response.headers["Content-Language"] == language
    assert client.get("/verein/mitglied/zugang").headers["Content-Language"] == language
    assert response.headers["Cache-Control"] == "no-store"
    for label in ("Deutsch", "English", "Français", "Italiano"):
        assert label in response.text


def test_unapproved_translation_has_no_advertised_indexing_alternate(web):
    web.config["SITE_RELEASE_ENABLED"] = True
    response = web.test_client().get("/verein?lang=en")
    assert 'rel="alternate"' not in response.text


def test_extended_policy_keeps_recipient_locale_outside_request(web):
    import locale_policy

    web.config["SITE_RELEASE_ENABLED"] = True
    with web.app_context():
        assert locale_policy.require_recipient_locale("it") == "it_CH"
        assert locale_policy.require_recipient_locale("en") == "en"
    assert locale_policy.normalize_locale("it") is None


def test_reviewed_public_locales_have_stable_self_canonicals_and_reciprocal_links(web):
    web.config.update(SITE_RELEASE_ENABLED=True, SITE_TRANSLATIONS_APPROVED=True)
    client = web.test_client()
    english = client.get("/verein?lang=en")
    assert 'rel="canonical" href="http://localhost/verein?lang=en"' in english.text
    for language in ("de-CH", "en", "fr-CH", "it-CH", "x-default"):
        assert 'hreflang="' + language + '"' in english.text
    german = client.get("/verein?lang=de-CH")
    assert 'rel="canonical" href="http://localhost/verein"' in german.text


def test_confirmation_language_switch_never_replays_credential(web):
    from i18n import language_switch_url

    web.config["SITE_RELEASE_ENABLED"] = True
    with web.test_request_context("/verein/bestaetigen/consumed-credential?lang=de"):
        destination = language_switch_url("it")
    assert destination == "/verein/mitglied/zugang?lang=it-CH"
    assert "consumed-credential" not in destination


def test_missing_media_variant_uses_shipped_image_and_records_explicit_fallback(web):
    from flask import g

    from i18n import localized_asset

    web.config["SITE_RELEASE_ENABLED"] = True
    with web.test_request_context("/open-source?lang=en"):
        assert (
            localized_asset("/static/images/product/resident-dashboard.gif")
            == "/static/images/product/resident-dashboard.gif"
        )
        assert g.untranslated_media is True
