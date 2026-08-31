"""Public website and authenticated product boundary contract."""

# SPDX-License-Identifier: AGPL-3.0-or-later

from pathlib import Path

import pytest

import app as app_module

ROOT = Path(__file__).resolve().parents[1]

_253_CHAR_HOSTNAME = ".".join(["a"] * 127)

PRODUCT_RULES = {
    "/",
    "/dashboard",
    "/dashboard/access/<token>",
    "/leg/dashboard",
    "/meter-upload",
    "/gemeinde/onboarding",
    "/gemeinde/dashboard",
    "/gemeinde/access/<token>",
    "/utility/login",
    "/admin/abrechnungen",
    "/api/v1/docs",
    "/api/v1/site/home",
    "/health",
    "/livez",
    "/unsubscribe",
}

PUBLIC_SITE_RULES = {
    "/public-preview",
    "/how-it-works",
    "/fuer-bewohner",
    "/fuer-gemeinden",
    "/open-source",
    "/leg-gruenden",
    "/leg-kalkulator",
    "/pricing",
    "/robots.txt",
    "/sitemap.xml",
    "/impressum",
    "/datenschutz",
    "/self-host",
    "/install.sh",
    "/rangliste",
    "/rangliste/fortschritte",
    "/rangliste/vergleich",
    "/rangliste/methodik",
    "/rangliste/badge/<int:bfs>.svg",
    "/rangliste/og/<int:bfs>.svg",
    "/gemeinde/verzeichnis",
    "/gemeinde/profil/<int:bfs>",
    "/pilotgemeinde/<slug>",
    "/leg-verzeichnis",
    "/leg-verzeichnis/<slug>",
    "/leg-check",
}

PRODUCT_TEMPLATES = {
    "dashboard.html",
    "leg_dashboard.html",
    "meter_upload.html",
    "registry_verify.html",
    "gemeinde/dashboard.html",
    "gemeinde/onboarding.html",
    "admin/abrechnungen.html",
    "api_docs.html",
    "unsubscribe.html",
}

PUBLIC_SITE_FILES = {
    "templates/base.html",
    "templates/index.html",
    "templates/how-it-works.html",
    "templates/fuer_bewohner.html",
    "templates/fuer_gemeinden.html",
    "templates/open_source.html",
    "templates/leg_gruenden.html",
    "templates/leg_kalkulator.html",
    "templates/pricing.html",
    "templates/impressum.html",
    "templates/datenschutz.html",
    "templates/self_host.html",
    "templates/sitemap.xml",
    "templates/gemeinde/rangliste.html",
    "templates/gemeinde/rangliste_fortschritte.html",
    "templates/gemeinde/vergleich.html",
    "templates/gemeinde/methodik.html",
    "templates/gemeinde/verzeichnis.html",
    "templates/gemeinde/profil.html",
    "templates/gemeinde/pilotgemeinde.html",
    "templates/leg_verzeichnis",
    "templates/partials/site_nav.html",
    "templates/partials/site_footer.html",
    "templates/partials/install_console.html",
    "templates/partials/leg_facts.html",
    "templates/partials/registry_trust.html",
    "templates/partials/data_provenance.html",
    "static/images/landing",
    "static/images/brand/flow-gemeinden.svg",
    "static/images/brand/flow-open-source.svg",
    "static/images/og-image.png",
    "static/js/install_console.js",
    "static/js/landing_segments.js",
    "pv_badge.py",
    "rangliste.py",
    "self_host.py",
}


def test_app_factory_registers_public_website_and_product_routes():
    application = app_module.create_app(
        {
            "TESTING": True,
            "RATELIMIT_STORAGE_URI": "memory://",
            "APP_BASE_URL": "http://localhost",
        },
        load_environment=False,
        check_database=False,
    )
    registered = {str(rule) for rule in application.url_map.iter_rules()}

    assert PRODUCT_RULES <= registered
    assert {
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
    } <= registered


def test_product_templates_use_dashboard_shell_without_public_navigation():
    for relative_path in PRODUCT_TEMPLATES:
        source = (ROOT / "templates" / relative_path).read_text(encoding="utf-8")
        assert '{% extends "product_base.html" %}' in source, relative_path
        assert "partials/site_nav.html" not in source, relative_path
        assert "partials/site_footer.html" not in source, relative_path

    shell = (ROOT / "templates/product_base.html").read_text(encoding="utf-8")
    assert "partials/site_nav.html" not in shell
    assert "partials/site_footer.html" not in shell


def test_restored_public_ranking_facade_renders_badges():
    import ranking

    assert hasattr(ranking.Ranking, "badge_svg")
    assert hasattr(ranking.Ranking, "og_card_svg")


def test_public_homepage_files_are_present_in_public_app_repository():
    required = {
        "templates/base.html",
        "templates/index.html",
        "templates/partials/site_nav.html",
        "templates/partials/site_footer.html",
        "static/images/landing",
        "static/images/og-image.png",
    }

    assert sorted(path for path in required if not (ROOT / path).exists()) == []


def test_public_site_links_use_the_configured_origin():
    application = app_module.create_app(
        {
            "TESTING": True,
            "RATELIMIT_STORAGE_URI": "memory://",
            "PUBLIC_SITE_URL": "https://www.openleg.ch/",
        },
        load_environment=False,
        check_database=False,
    )

    assert application.jinja_env.globals["public_site_url"]("/how-it-works") == (
        "https://www.openleg.ch/how-it-works"
    )


@pytest.mark.parametrize(
    "path",
    (
        "https://attacker.example/path",
        "//attacker.example/path",
        "///https://attacker.example/path",
        "/javascript:alert(1)",
    ),
)
def test_public_site_links_reject_external_paths(path):
    application = app_module.create_app(
        {
            "TESTING": True,
            "RATELIMIT_STORAGE_URI": "memory://",
            "PUBLIC_SITE_URL": "https://www.openleg.ch/",
        },
        load_environment=False,
        check_database=False,
    )

    with pytest.raises(ValueError, match="relative path"):
        application.jinja_env.globals["public_site_url"](path)


@pytest.mark.parametrize(
    "public_site_url, expected_message",
    [
        ("openleg.ch", r"absolute HTTP\(S\) URL"),
        ("https://:443", r"absolute HTTP\(S\) URL"),
        (" https://openleg.ch", r"absolute HTTP\(S\) URL"),
        ("https://openleg.ch ", r"absolute HTTP\(S\) URL"),
        ("https://openleg.ch\n", r"absolute HTTP\(S\) URL"),
        ("https://[::1", r"absolute HTTP\(S\) URL"),
        ("https://user:secret@openleg.ch", r"credentials or suffixes"),
        ("https://@openleg.ch", r"credentials or suffixes"),
        ("https://:@openleg.ch", r"credentials or suffixes"),
        ("https://openleg.ch/path", r"credentials or suffixes"),
        ("https://openleg.ch?next=dashboard", r"credentials or suffixes"),
        ("https://openleg.ch#dashboard", r"credentials or suffixes"),
        ("https://openleg.ch/;", r"credentials or suffixes"),
        ("https://openleg.ch?", r"credentials or suffixes"),
        ("https://openleg.ch#", r"credentials or suffixes"),
        ("https://openleg.ch:bad", r"valid port"),
        ("https://openleg.ch:", r"valid port"),
        ("https://openleg.ch:0", r"valid port"),
        ("https://open_leg.ch", r"valid hostname"),
        ("https://_openleg.ch", r"valid hostname"),
        ("https://openleg.ch%2Fevil.example", r"valid hostname"),
        (f"https://{_253_CHAR_HOSTNAME}.a", r"valid hostname"),
    ],
    ids=[
        "bare_hostname",
        "empty_hostname_with_port",
        "leading_whitespace",
        "trailing_whitespace",
        "trailing_newline",
        "malformed_ipv6",
        "credentials",
        "empty_username",
        "empty_userinfo",
        "path_suffix",
        "query_suffix",
        "fragment_suffix",
        "semicolon_path_suffix",
        "empty_query",
        "empty_fragment",
        "non_numeric_port",
        "empty_port",
        "port_zero",
        "underscore_in_label",
        "leading_underscore",
        "percent_encoded_hostname",
        "hostname_exceeds_253_chars",
    ],
)
def test_public_site_origin_rejects_unsafe_values(public_site_url, expected_message):
    with pytest.raises(ValueError, match=expected_message):
        app_module.create_app(
            {
                "TESTING": True,
                "RATELIMIT_STORAGE_URI": "memory://",
                "PUBLIC_SITE_URL": public_site_url,
            },
            load_environment=False,
            check_database=False,
        )


def test_public_site_origin_malformed_ipv6_error_has_value_error_cause():
    with pytest.raises(ValueError, match=r"absolute HTTP\(S\) URL") as exc_info:
        app_module.create_app(
            {
                "TESTING": True,
                "RATELIMIT_STORAGE_URI": "memory://",
                "PUBLIC_SITE_URL": "https://[::1",
            },
            load_environment=False,
            check_database=False,
        )
    assert isinstance(exc_info.value.__cause__, ValueError)


@pytest.mark.parametrize(
    "public_site_url, path, expected_url",
    [
        (
            "https://www.openleg.ch/",
            "/how-it-works",
            "https://www.openleg.ch/how-it-works",
        ),
        (
            "https://openleg.ch:443/",
            "/how-it-works",
            "https://openleg.ch:443/how-it-works",
        ),
        (
            "https://127.0.0.1:443/",
            "/how-it-works",
            "https://127.0.0.1:443/how-it-works",
        ),
        ("https://[::1]:443/", "/how-it-works", "https://[::1]:443/how-it-works"),
        (
            "https://münchen.example/",
            "/how-it-works",
            "https://münchen.example/how-it-works",
        ),
        (
            "https://openleg.ch./",
            "/how-it-works",
            "https://openleg.ch./how-it-works",
        ),
        (
            "https://openleg.ch.:443/",
            "/how-it-works",
            "https://openleg.ch.:443/how-it-works",
        ),
        (
            f"https://{_253_CHAR_HOSTNAME}./",
            "/how-it-works",
            f"https://{_253_CHAR_HOSTNAME}./how-it-works",
        ),
    ],
    ids=[
        "ordinary_domain",
        "explicit_port",
        "ipv4",
        "bracketed_ipv6",
        "idn",
        "trailing_dot_fqdn",
        "trailing_dot_fqdn_with_port",
        "max_length_fqdn_with_trailing_dot",
    ],
)
def test_public_site_origin_accepts_safe_values(public_site_url, path, expected_url):
    application = app_module.create_app(
        {
            "TESTING": True,
            "RATELIMIT_STORAGE_URI": "memory://",
            "PUBLIC_SITE_URL": public_site_url,
        },
        load_environment=False,
        check_database=False,
    )

    assert application.jinja_env.globals["public_site_url"](path) == expected_url
