# SPDX-License-Identifier: AGPL-3.0-or-later
"""Trust surface: pilot case-study page with real federal data (issue #108)."""

import importlib
import json
import os
import re
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

import municipality as municipality_module

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BADEN_PROFILE = {
    "bfs_number": 4021,
    "name": "Baden",
    "kanton": "AG",
    "population": 20000,
    "pv_score_pct": 42.7,
}

BADEN_H4_TARIFF = {
    "bfs_number": 4021,
    "year": 2026,
    "operator_name": "Regionalwerke AG Baden",
    "category": "H4",
    "total_rp_kwh": 27.5,
    "energy_rp_kwh": 12.0,
    "grid_rp_kwh": 9.5,
}

BADEN_SONNENDACH = {
    "bfs_number": 4021,
    "total_roof_area_m2": 900000.0,
    "suitable_roof_area_m2": 540000.0,
    "utilization_pct": 12.4,
}


def _make_client():
    app = Flask(__name__, template_folder=os.path.join(PROJECT_ROOT, "templates"))
    app.config["TESTING"] = True
    app.register_blueprint(municipality_module.municipality_bp)
    app.register_blueprint(municipality_module.pilot_bp)
    return app.test_client()


def _patch_pilot_deps(monkeypatch, profile=None):
    monkeypatch.setattr(
        municipality_module.db,
        "get_municipality_profile",
        lambda _bfs: dict(BADEN_PROFILE) if profile is None else profile,
    )
    monkeypatch.setattr(
        municipality_module.db,
        "get_elcom_tariffs",
        lambda *_a, **_k: [dict(BADEN_H4_TARIFF)],
    )
    monkeypatch.setattr(
        municipality_module.db,
        "get_sonnendach_municipal",
        lambda _bfs: dict(BADEN_SONNENDACH),
    )
    monkeypatch.setattr(
        municipality_module.Ranking,
        "load",
        lambda: municipality_module.Ranking([dict(BADEN_PROFILE)]),
    )


def _get_html(monkeypatch, path="/pilotgemeinde/baden"):
    client = _make_client()
    _patch_pilot_deps(monkeypatch)
    resp = client.get(path, headers={"Host": "openleg.ch"})
    assert resp.status_code == 200
    return resp.data.decode("utf-8", errors="ignore")


def _jsonld_nodes(html):
    blocks = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', html, flags=re.DOTALL
    )
    assert blocks, "no JSON-LD block on case-study page"
    data = json.loads(blocks[0])
    nodes = data.get("@graph", [data])
    return {node.get("@type"): node for node in nodes}


def test_pilot_baden_renders_trust_content(monkeypatch):
    html = _get_html(monkeypatch)
    assert "Baden" in html
    assert "Regionalwerke AG Baden" in html
    # Concrete CHF savings from the real H4 grid tariff (9.5 * 40% * 4500 kWh).
    assert "171" in html
    # Federal data sources named.
    assert "ElCom" in html
    assert "Sonnendach" in html


def test_pilot_baden_has_unhappy_path_faq(monkeypatch):
    html = _get_html(monkeypatch)
    assert "Austritt" in html
    assert "ElCom" in html
    # No fabricated social proof.
    assert "Testimonial" not in html


def test_pilot_baden_jsonld_article_and_place(monkeypatch):
    html = _get_html(monkeypatch)
    nodes = _jsonld_nodes(html)
    article = nodes.get("Article")
    assert article, f"no Article node, got {list(nodes)}"
    assert "Baden" in article.get("headline", "")
    place = nodes.get("Place")
    assert place, "no Place node"
    assert place["name"] == "Baden"


def test_pilot_baden_canonical(monkeypatch):
    html = _get_html(monkeypatch)
    assert 'rel="canonical" href="http://openleg.ch/pilotgemeinde/baden"' in html


def test_pilot_unknown_slug_is_404(monkeypatch):
    client = _make_client()
    _patch_pilot_deps(monkeypatch)
    resp = client.get("/pilotgemeinde/atlantis")
    assert resp.status_code == 404


def test_profil_links_to_case_study(monkeypatch):
    client = _make_client()
    _patch_pilot_deps(monkeypatch)
    resp = client.get("/gemeinde/profil/4021")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert "/pilotgemeinde/baden" in html


@pytest.fixture
def full_app_module():
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": "postgresql://x:x@localhost/x",
            "REDIS_URL": "memory://",
            "CRON_SECRET": "test-cron-secret",
            "APP_BASE_URL": "http://localhost:5003",
        },
    ):
        with (
            patch("database.is_db_available", return_value=True),
            patch("database._connection_pool", MagicMock()),
        ):
            import app as app_module

            app_module = importlib.reload(app_module)
            yield app_module


def test_sitemap_contains_case_study(full_app_module, monkeypatch):
    monkeypatch.setattr(
        full_app_module.db,
        "get_all_municipality_profile_bfs_numbers",
        lambda: [4021],
    )
    client = full_app_module.app.test_client()
    xml = client.get("/sitemap.xml").data.decode("utf-8", errors="ignore")
    assert "/pilotgemeinde/baden" in xml


def test_pilot_uses_latest_tariffs_not_hardcoded_year(monkeypatch):
    client = _make_client()
    _patch_pilot_deps(monkeypatch)
    seen_years = []

    def _spy_tariffs(_bfs, year=None):
        seen_years.append(year)
        return [dict(BADEN_H4_TARIFF)]

    monkeypatch.setattr(municipality_module.db, "get_elcom_tariffs", _spy_tariffs)
    resp = client.get("/pilotgemeinde/baden")
    assert resp.status_code == 200
    assert seen_years == [None], (
        "pilot route must not hardcode a tariff year; latest-first ordering "
        f"of get_elcom_tariffs supplies the newest data (got {seen_years})"
    )


def test_fuer_bewohner_links_to_case_study(full_app_module):
    client = full_app_module.app.test_client()
    resp = client.get("/fuer-bewohner")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert "/pilotgemeinde/baden" in html


def test_pilot_route_delegates_to_municipality_profile_module(monkeypatch):
    """Route parses request, delegates assembly to municipality_profile, renders (#209)."""
    client = _make_client()
    fake_ctx = {
        "profile": dict(BADEN_PROFILE),
        "bfs": 4021,
        "slug": "baden",
        "h4": dict(BADEN_H4_TARIFF),
        "solar": dict(BADEN_SONNENDACH),
        "value_gap": {"annual_savings_chf": 171.0},
        "json_ld": {"@context": "https://schema.org"},
        "site_url": "http://openleg.ch",
        "canonical_path": "/pilotgemeinde/baden",
    }
    calls = []

    def _fake_pilot_context(slug, *, site_url):
        calls.append({"slug": slug, "site_url": site_url})
        return fake_ctx

    monkeypatch.setattr(
        municipality_module.municipality_profile, "pilot_context", _fake_pilot_context
    )

    rendered = {}

    def _fake_render_template(template_name, **context):
        rendered["template"] = template_name
        rendered["context"] = context
        return "ok"

    monkeypatch.setattr(municipality_module, "render_template", _fake_render_template)

    resp = client.get("/pilotgemeinde/baden", headers={"Host": "openleg.ch"})

    assert resp.status_code == 200
    assert calls == [{"slug": "baden", "site_url": "http://openleg.ch"}]
    assert rendered["template"] == "gemeinde/pilotgemeinde.html"


def test_pilot_route_returns_404_when_context_is_none(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(
        municipality_module.municipality_profile,
        "pilot_context",
        lambda slug, *, site_url: None,
    )

    resp = client.get("/pilotgemeinde/atlantis")

    assert resp.status_code == 404
