# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for stakeholder wayfinding: landing pathways, resident page, profile rebrand."""

import importlib
import os
from unittest.mock import MagicMock, patch

import pytest

TEMPLATES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"
)


def _read(*parts):
    with open(os.path.join(TEMPLATES, *parts), encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture
def full_app_module():
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": "postgresql://x:x@localhost/x",
            "REDIS_URL": "memory://",
            "APP_BASE_URL": "http://localhost:5003",
        },
    ):
        with (
            patch("database.is_db_available", return_value=True),
            patch("database._connection_pool", MagicMock()),
        ):
            import app as app_module

            app_module = importlib.reload(app_module)
            hooks = list(app_module.app.before_request_funcs.get(None, []))
            app_module.app.before_request_funcs[None] = [
                hook
                for hook in hooks
                if not (
                    getattr(hook, "__module__", "").startswith("flask_limiter")
                    or getattr(hook, "__name__", "") == "_check_request_limit"
                )
            ]
            try:
                yield app_module
            finally:
                app_module.app.before_request_funcs[None] = hooks


# --- Landing pathways wayfinding ---


def test_landing_has_four_stakeholder_pathways():
    html = _read("index.html")
    assert "Für wen ist OpenLEG?" in html
    for href in ("/fuer-bewohner", "/leg-gruenden", "/fuer-gemeinden", "/open-source"):
        assert f'href="{href}"' in html
    assert "Bewohner und Gründer" in html
    assert "LEG-Betreiber" in html
    assert "Entwickler und Self-Hosting" in html


# --- Resident pathway page ---


def test_fuer_bewohner_route_renders(full_app_module):
    client = full_app_module.app.test_client()
    resp = client.get("/fuer-bewohner", follow_redirects=True)
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert "Für Bewohner und Gründer" in html
    assert '<link rel="canonical" href="' in html
    assert "/fuer-bewohner" in html
    assert 'property="og:title"' in html
    assert 'property="og:url"' in html
    assert 'name="twitter:card" content="summary_large_image"' in html
    assert '"@type": "BreadcrumbList"' in html


def test_fuer_bewohner_template_is_on_brand_and_funnels():
    html = _read("fuer_bewohner.html")
    base = _read("base.html")
    assert "cdn.tailwindcss.com" not in html
    assert '{% extends "base.html" %}' in html
    assert '{% from "partials/page_meta.html" import page_meta with context %}' in html
    assert "partials/tailwind_brand.html" not in html
    assert "partials/site_nav.html" not in html
    assert "partials/site_footer.html" not in html
    assert "partials/tailwind_brand.html" in base
    assert "partials/site_nav.html" in base
    assert "partials/site_footer.html" in base
    assert "page_meta(" in html
    assert 'og_image="/static/images/og-image.png"' in html
    assert '"@type": "BreadcrumbList"' in html
    # funnels to the conversion surfaces
    assert 'href="/leg-kalkulator"' in html
    assert 'href="/leg-gruenden"' in html
    assert 'href="/#registrieren"' in html


def test_fuer_bewohner_template_uses_shared_base():
    html = _read("fuer_bewohner.html")
    assert "<!DOCTYPE html>" not in html


@pytest.mark.parametrize(
    "template_name",
    [
        "index.html",
        "fuer_gemeinden.html",
        "open_source.html",
        "how-it-works.html",
        "leg_gruenden.html",
        "leg_kalkulator.html",
        "pricing.html",
        "impressum.html",
        "datenschutz.html",
    ],
)
def test_simple_public_pages_extend_shared_base(template_name):
    html = _read(template_name)
    assert '{% extends "base.html" %}' in html
    assert '{% from "partials/page_meta.html" import page_meta with context %}' in html


def test_fuer_bewohner_in_sitemap(full_app_module, monkeypatch):
    monkeypatch.setattr(
        full_app_module.db, "get_all_municipality_profile_bfs_numbers", lambda: [4021]
    )
    client = full_app_module.app.test_client()
    xml = client.get("/sitemap.xml", follow_redirects=True).data.decode(
        "utf-8", errors="ignore"
    )
    assert "/fuer-bewohner" in xml


# --- Profile page rebrand + resident CTA ---


def test_profil_uses_shared_design_system():
    html = _read("gemeinde", "profil.html")
    assert "cdn.tailwindcss.com" not in html
    assert "partials/tailwind_brand.html" in html
    assert "partials/site_nav.html" in html
    assert "partials/site_footer.html" in html


def test_profil_has_resident_conversion_path():
    html = _read("gemeinde", "profil.html")
    assert "Wohnen Sie in" in html
    assert 'href="/fuer-bewohner"' in html
    assert 'href="/leg-kalkulator"' in html


# --- README stakeholder paths ---


def test_readme_choose_your_path():
    path = os.path.join(os.path.dirname(TEMPLATES), "README.md")
    with open(path, encoding="utf-8") as handle:
        readme = handle.read()
    assert "Choose your path" in readme
    assert "/fuer-bewohner" in readme
    assert "/gemeinde/profil/4021" in readme
