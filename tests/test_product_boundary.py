"""Product/public-site repository boundary contract."""

# SPDX-License-Identifier: AGPL-3.0-or-later

from pathlib import Path

import pytest

import app as app_module

ROOT = Path(__file__).resolve().parents[1]

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
    "rangliste.py",
    "self_host.py",
}


def test_app_factory_registers_product_routes_only():
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
    assert PUBLIC_SITE_RULES.isdisjoint(registered)


def test_product_templates_use_dashboard_shell_without_public_navigation():
    for relative_path in PRODUCT_TEMPLATES:
        source = (ROOT / "templates" / relative_path).read_text(encoding="utf-8")
        assert '{% extends "product_base.html" %}' in source, relative_path

    shell = (ROOT / "templates/product_base.html").read_text(encoding="utf-8")
    assert "partials/site_nav.html" not in shell
    assert "partials/site_footer.html" not in shell


def test_public_site_files_are_absent_from_product_repository():
    remaining = sorted(path for path in PUBLIC_SITE_FILES if (ROOT / path).exists())

    assert remaining == []


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
    "path", ("https://attacker.example/path", "//attacker.example/path")
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
    "value",
    (
        "openleg.ch",
        "https://user:secret@openleg.ch",
        "https://openleg.ch/path",
        "https://openleg.ch?next=dashboard",
        "https://openleg.ch#dashboard",
    ),
)
def test_public_site_origin_rejects_unsafe_or_ambiguous_values(value):
    with pytest.raises(ValueError, match="PUBLIC_SITE_URL"):
        app_module.create_app(
            {
                "TESTING": True,
                "RATELIMIT_STORAGE_URI": "memory://",
                "PUBLIC_SITE_URL": value,
            },
            load_environment=False,
            check_database=False,
        )
