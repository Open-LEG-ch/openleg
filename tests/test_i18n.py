# SPDX-License-Identifier: AGPL-3.0-or-later
"""Language selection through the real application and safe gettext output."""

import gettext
import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from flask import Flask, render_template_string
from flask_babel import force_locale, ngettext
from flask_babel import gettext as _

# Pin import-time dependency settings as well as build_config/request settings.
# Clearing the mapping also prevents newly introduced settings from inheriting
# host values. patch.dict restores the caller's entire environment on exit.
_TEST_ENVIRONMENT = {
    "DATABASE_URL": "",
    "DB_POOL_MIN": "2",
    "DB_POOL_MAX": "10",
    "REDIS_URL": "",
    "SMTP_HOST": "localhost",
    "SMTP_PORT": "587",
    "SMTP_USER": "",
    "SMTP_PASSWORD": "",
    "FROM_EMAIL": "noreply@example.invalid",
    "DEEPSIGN_API_URL": "https://example.invalid",
    "DEEPSIGN_API_KEY": "",
    "DEEPSIGN_WEBHOOK_SECRET": "",
    "SECRET_KEY": "i18n-test-only",
    "APP_BASE_URL": "http://localhost",
    "PUBLIC_SITE_URL": "https://openleg.ch",
    "ALLOWED_HOSTS": "localhost,127.0.0.1",
    "ADMIN_EMAIL": "admin@example.invalid",
    "ADMIN_TOKEN": "",
    "INTERNAL_TOKEN": "",
    "CRON_SECRET": "",
    "AGENTMAIL_WEBHOOK_SECRET": "",
    "SESSION_COOKIE_SECURE": "false",
    "SESSION_COOKIE_SAMESITE": "Lax",
    "PERMANENT_SESSION_LIFETIME": "3600",
    "DASHBOARD_ACCESS_TOKEN_TTL_SECONDS": "900",
    "DASHBOARD_EMAIL_TOKEN_TTL_SECONDS": "86400",
    "BABEL_DEFAULT_LOCALE": "de_CH",
    "MATOMO_SITE_ID": "",
    "GA4_MEASUREMENT_ID": "",
}


@contextmanager
def _synthetic_environment():
    with patch.dict(os.environ, _TEST_ENVIRONMENT, clear=True):
        yield


@pytest.fixture(autouse=True)
def isolated_environment():
    with _synthetic_environment():
        yield


@pytest.fixture
def app_module(isolated_environment):
    # Import only after isolation, including in a fresh pytest process.
    import app

    return app


@pytest.fixture
def web(app_module):
    return app_module.create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "i18n-test-only",
            "APP_BASE_URL": "http://localhost",
            "PUBLIC_SITE_URL": "https://openleg.ch",
            "RATELIMIT_STORAGE_URI": "memory://",
            "SESSION_COOKIE_SECURE": False,
            "BABEL_DEFAULT_LOCALE": "de_CH",
        },
        load_environment=False,
        check_database=False,
    )


def test_default_language_remains_german(web):
    response = web.test_client().get("/login", headers={"Accept-Language": "fr-CH"})
    assert response.status_code == 200
    assert '<html lang="de-CH">' in response.text
    assert response.headers["Content-Language"] == "de-CH"


def test_french_switch_persists_across_navigation_and_can_be_reset(web):
    client = web.test_client()
    french = client.get("/login?lang=fr-CH")
    assert french.status_code == 200
    assert '<html lang="fr-CH">' in french.text
    assert french.headers["Content-Language"] == "fr-CH"
    assert "Cookie" in french.vary
    assert client.get("/login").headers["Content-Language"] == "fr-CH"
    assert client.get("/login?lang=de").headers["Content-Language"] == "de-CH"


@pytest.mark.parametrize(
    "language", ["../../fr", "it", "fr<script>", "https://evil.invalid"]
)
def test_unsupported_language_cannot_select_a_catalog_or_redirect(web, language):
    response = web.test_client().get("/login", query_string={"lang": language})
    assert response.status_code == 200
    assert response.headers["Content-Language"] == "de-CH"
    assert not response.headers.get("Location")


def test_switch_links_preserve_repeated_query_values_and_stay_local(web):
    from i18n import language_switch_url

    with web.test_request_context("/login?ref=a%26b&filter=x&filter=y&lang=de"):
        target = urlsplit(language_switch_url("fr-CH"))
    assert not target.netloc and not target.scheme
    assert target.path == "/login"
    assert parse_qs(target.query) == {
        "ref": ["a&b"],
        "filter": ["x", "y"],
        "lang": ["fr-CH"],
    }


def test_french_catalog_and_fallback_are_used_and_interpolation_is_escaped(web):
    with web.test_request_context("/?lang=fr"):
        assert _("Sprache") == "Langue"
        assert _("Untranslated sentinel") == "Untranslated sentinel"
        rendered = render_template_string(
            "{{ _('Hallo %(name)s', name=name) }}", name="<script>x</script>"
        )
        assert "<script>" not in rendered
        assert "&lt;script&gt;" in rendered


def test_locale_is_scoped_to_the_app_and_request(web):
    with web.test_request_context("/?lang=fr"):
        assert _("Sprache") == "Langue"
    with web.test_request_context("/"):
        assert _("Sprache") == "Sprache"
    with web.app_context(), force_locale("fr_CH"):
        assert _("Sprache") == "Langue"
    assert _("Sprache") == "Sprache"


def test_real_catalog_compiles_with_french_plural_rules(web):
    root = Path(__file__).resolve().parents[1]
    with (root / "translations/fr_CH/LC_MESSAGES/messages.mo").open("rb") as handle:
        catalog = gettext.GNUTranslations(handle)
    assert catalog.gettext("Sprache") == "Langue"
    with web.test_request_context("/?lang=fr"):
        assert (
            ngettext("%(num)s Interessierte", "%(num)s Interessierte", 1)
            == "1 personne intéressée"
        )
        assert (
            ngettext("%(num)s Interessierte", "%(num)s Interessierte", 2)
            == "2 personnes intéressées"
        )


def test_i18n_initialization_does_not_require_an_application_factory():
    from i18n import init_app

    standalone = Flask(__name__)
    standalone.secret_key = "isolated-test"
    init_app(standalone)
    with standalone.test_request_context("/"):
        assert _("Sprache") == "Sprache"


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "de-CH"),
        ("", "de-CH"),
        ("  ", "de-CH"),
        ("fr_CH ", "fr-CH"),
        ("de_CH", "de-CH"),
        ("fr-CH", "fr-CH"),
    ],
)
def test_factory_uses_normalized_environment_display_locale(
    app_module, monkeypatch, raw, expected
):
    _locale_factory(app_module, monkeypatch, raw, None, expected)


@pytest.mark.parametrize(
    "override,expected",
    [
        ("", "de-CH"),
        (" ", "de-CH"),
        (" de_CH ", "de-CH"),
        ("fr_CH ", "fr-CH"),
        ("fr-CH", "fr-CH"),
    ],
)
def test_factory_normalizes_explicit_display_locale_override(
    app_module, monkeypatch, override, expected
):
    _locale_factory(app_module, monkeypatch, "fr_CH", override, expected)


@pytest.mark.parametrize("invalid", ["not-a-locale", "it_CH", "fr_CH/../../"])
@pytest.mark.parametrize("source", ["environment", "override"])
def test_factory_still_rejects_unsupported_display_locale(
    app_module, monkeypatch, invalid, source
):
    with pytest.raises(ValueError, match="BABEL_DEFAULT_LOCALE must be"):
        _locale_factory(
            app_module,
            monkeypatch,
            invalid if source == "environment" else "de_CH",
            invalid if source == "override" else None,
            None,
        )


def test_factory_rejects_non_string_locale_override(app_module, monkeypatch):
    with pytest.raises(ValueError, match="BABEL_DEFAULT_LOCALE must be"):
        _locale_factory(app_module, monkeypatch, "de_CH", 123, None)


def _locale_factory(app_module, monkeypatch, raw, override, expected):
    if raw is None:
        monkeypatch.delenv("BABEL_DEFAULT_LOCALE", raising=False)
    else:
        monkeypatch.setenv("BABEL_DEFAULT_LOCALE", raw)
    config = {
        "TESTING": True,
        "SECRET_KEY": "synthetic-locale-default",
        "APP_BASE_URL": "http://localhost",
        "PUBLIC_SITE_URL": "http://localhost",
        "RATELIMIT_STORAGE_URI": "memory://",
        "SESSION_COOKIE_SECURE": False,
    }
    if override is not None:
        config["BABEL_DEFAULT_LOCALE"] = override
    web = app_module.create_app(config, load_environment=False, check_database=False)
    response = web.test_client().get("/login")
    assert response.status_code == 200
    assert response.headers["Content-Language"] == expected
    assert f'<html lang="{expected}">' in response.text


def test_localized_links_preserve_tokens_filters_fragments_and_german_defaults(web):
    from i18n import localized_url

    original = "https://example.ch/dashboard/access/opaque?ref=a%26b&filter=x#content"
    with web.test_request_context("/?lang=fr"):
        french = urlsplit(localized_url(original))
    assert french.scheme == "https" and french.netloc == "example.ch"
    assert french.path == "/dashboard/access/opaque" and french.fragment == "content"
    assert parse_qs(french.query) == {
        "ref": ["a&b"],
        "filter": ["x"],
        "lang": ["fr-CH"],
    }
    with web.test_request_context("/"):
        assert localized_url(original) == original


def test_default_locale_alias_is_normalized_before_babel_parses_it():
    from i18n import init_app

    standalone = Flask(__name__)
    standalone.config.update(SECRET_KEY="test-only", BABEL_DEFAULT_LOCALE="fr-CH")
    init_app(standalone)
    assert standalone.config["BABEL_DEFAULT_LOCALE"] == "fr_CH"


def test_links_to_the_public_site_carry_the_selected_language(web):
    public_site_url = web.jinja_env.globals["public_site_url"]
    with web.test_request_context("/?lang=fr"):
        assert (
            public_site_url("/how-it-works")
            == "https://openleg.ch/how-it-works?lang=fr-CH"
        )
    with web.test_request_context("/"):
        assert public_site_url("/how-it-works") == "https://openleg.ch/how-it-works"


@pytest.mark.parametrize("default", ["de_CH", "fr_CH"])
@pytest.mark.parametrize("selected", ["de_CH", "fr_CH"])
def test_durable_links_keep_an_explicit_recipient_locale(web, default, selected):
    from i18n import localized_url

    web.config["BABEL_DEFAULT_LOCALE"] = default
    original = "https://example.ch/dashboard/access/opaque?filter=x&filter=y#detail"
    with web.app_context():
        target = localized_url(original, locale=selected)
    parsed = urlsplit(target)
    assert parsed.path == "/dashboard/access/opaque"
    assert parsed.fragment == "detail"
    assert parse_qs(parsed.query) == {
        "filter": ["x", "y"],
        "lang": [selected.replace("_", "-")],
    }
    fresh = web.test_client().get("/login?" + parsed.query)
    assert fresh.headers["Content-Language"] == selected.replace("_", "-")


def test_german_choice_on_french_default_is_kept_in_generated_link(web):
    from i18n import localized_url

    web.config["BABEL_DEFAULT_LOCALE"] = "fr_CH"
    with web.test_request_context("/?lang=de-CH"):
        assert localized_url("https://example.ch/login") == (
            "https://example.ch/login?lang=de-CH"
        )


def test_generated_link_replaces_a_stale_language_with_current_choice(web):
    from i18n import localized_url

    with web.test_request_context("/?lang=de-CH"):
        assert localized_url("https://example.ch/login?lang=fr-CH") == (
            "https://example.ch/login?lang=de-CH"
        )


@pytest.mark.parametrize(
    "locale,expected", [("de-CH", "50’000"), ("fr-CH", "50\u202f000")]
)
def test_display_counts_use_swiss_locale_without_changing_the_value(
    web, locale, expected
):
    from decimal import Decimal

    from i18n import format_number_label

    value = Decimal(50000)
    with web.test_request_context("/?lang=" + locale):
        assert format_number_label(value) == expected
        assert "12345" in format_number_label(Decimal("50000.12345"))
    assert value == Decimal(50000)


@pytest.mark.parametrize(
    "sequence", [("de-CH", "fr-CH", "de-CH"), ("fr-CH", "de-CH", "fr-CH")]
)
def test_cached_templates_keep_locale_dependent_filters_request_local(web, sequence):
    from flask import render_template
    from jinja2 import ChoiceLoader, DictLoader

    web.jinja_loader = ChoiceLoader(
        [
            DictLoader(
                {
                    "locale-cache.html": "{{ '/static/images/product/resident-dashboard.gif'|localized_asset }}|{{ 4500|numberlabel }}|{{ 'Juli 2026'|periodlabel }}|{{ '15.00 Rp./kWh'|ratelabel }}|{{ '/dashboard'|localized_url }}"
                }
            ),
            web.jinja_loader,
        ]
    )
    for language in sequence:
        with web.test_request_context("/?lang=" + language):
            rendered = render_template("locale-cache.html")
        asset, count, period, rate, url = rendered.split("|")
        french = language == "fr-CH"
        assert ("/fr-CH/" in asset) == french
        assert count == ("4\u202f500" if french else "4’500")
        assert period == ("juillet 2026" if french else "Juli 2026")
        assert rate == ("15.00 ct./kWh" if french else "15.00 Rp./kWh")
        assert url == ("/dashboard?lang=fr-CH" if french else "/dashboard")


def test_locale_fixture_pins_effective_configuration(web):
    """Host values must not change sessions, auth, URLs or the default locale."""
    from datetime import timedelta

    assert web.config["PERMANENT_SESSION_LIFETIME"] == timedelta(seconds=3600)
    assert web.config["SECRET_KEY"] == "i18n-test-only"
    assert web.config["SESSION_COOKIE_SECURE"] is False
    assert web.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert web.config["DASHBOARD_ACCESS_TOKEN_TTL_SECONDS"] == 900
    assert web.config["DASHBOARD_EMAIL_TOKEN_TTL_SECONDS"] == 86400
    assert web.config["APP_BASE_URL"] == "http://localhost"
    assert web.config["PUBLIC_SITE_URL"] == "https://openleg.ch"
    assert web.config["ALLOWED_HOSTS"] == ["localhost", "127.0.0.1"]
    assert web.config["MATOMO_SITE_ID"] == ""
    assert web.config["BABEL_DEFAULT_LOCALE"] == "de_CH"
    for name in ("ADMIN_TOKEN", "INTERNAL_TOKEN", "CRON_SECRET", "GA4_MEASUREMENT_ID"):
        assert os.environ[name] == ""
    assert "UNRECOGNIZED_HOST_SETTING" not in os.environ
    client = web.test_client()
    assert client.get("/login?lang=fr-CH").headers["Content-Language"] == "fr-CH"
    assert client.get("/login").headers["Content-Language"] == "fr-CH"
    assert client.get("/admin/overview").status_code == 404
    assert (
        client.get(
            "/admin/overview", headers={"X-Admin-Token": "synthetic-outside-admin"}
        ).status_code
        == 404
    )


@pytest.mark.parametrize("raises", [False, True])
def test_synthetic_environment_restores_caller_even_on_error(monkeypatch, raises):
    monkeypatch.setenv("PERMANENT_SESSION_LIFETIME", "sentinel")
    monkeypatch.setenv("BABEL_DEFAULT_LOCALE", "it_CH")
    monkeypatch.setenv("UNRECOGNIZED_HOST_SETTING", "outside")
    before = dict(os.environ)

    def exercise():
        with _synthetic_environment():
            assert dict(os.environ) == _TEST_ENVIRONMENT
            os.environ["BABEL_DEFAULT_LOCALE"] = "fr_CH"
            if raises:
                raise RuntimeError("synthetic test failure")

    if raises:
        with pytest.raises(RuntimeError, match="synthetic test failure"):
            exercise()
    else:
        exercise()
    assert dict(os.environ) == before


def test_invalid_configuration_inside_isolation_is_not_hidden(app_module, monkeypatch):
    monkeypatch.setenv("PERMANENT_SESSION_LIFETIME", "sentinel")
    with pytest.raises(ValueError, match="invalid literal for int"):
        app_module.create_app(load_environment=False, check_database=False)
