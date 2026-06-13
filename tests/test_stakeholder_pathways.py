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
    resp = client.get("/fuer-bewohner")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert "Für Bewohner und Gründer" in html


def test_fuer_bewohner_template_is_on_brand_and_funnels():
    html = _read("fuer_bewohner.html")
    assert "cdn.tailwindcss.com" not in html
    assert "partials/tailwind_brand.html" in html
    assert "partials/site_nav.html" in html
    assert "partials/site_footer.html" in html
    # share metadata
    assert '<meta name="description"' in html
    assert 'rel="canonical"' in html
    assert 'property="og:title"' in html
    assert '"@type": "BreadcrumbList"' in html
    # funnels to the conversion surfaces
    assert 'href="/leg-kalkulator"' in html
    assert 'href="/leg-gruenden"' in html
    assert 'href="/#registrieren"' in html


def test_fuer_bewohner_in_sitemap(full_app_module, monkeypatch):
    monkeypatch.setattr(
        full_app_module.db, "get_all_municipality_profile_bfs_numbers", lambda: [4021]
    )
    client = full_app_module.app.test_client()
    xml = client.get("/sitemap.xml").data.decode("utf-8", errors="ignore")
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
