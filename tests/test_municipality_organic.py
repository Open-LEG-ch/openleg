# SPDX-License-Identifier: AGPL-3.0-or-later
"""Organic-growth focused tests for municipality routes."""

import os

from flask import Flask

import municipality as municipality_module

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _make_client():
    app = Flask(__name__, template_folder=os.path.join(PROJECT_ROOT, "templates"))
    app.config["TESTING"] = True
    app.register_blueprint(municipality_module.municipality_bp)
    return app.test_client()


def test_onboarding_renders_typeahead_search():
    client = _make_client()
    resp = client.get("/gemeinde/onboarding")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert "Gemeinde suchen" in html
    assert "municipality-search" in html


def test_onboarding_preserves_controls_actions_and_accessibility():
    client = _make_client()
    resp = client.get("/gemeinde/onboarding")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    for control_id in (
        "municipality-search",
        "selected-bfs",
        "selected-name",
        "admin-email",
        "contact-name",
    ):
        assert f'id="{control_id}"' in html
    for labelled_id in ("municipality-search", "admin-email", "contact-name"):
        assert f'for="{labelled_id}"' in html
    for live_id in ("selected-municipality", "form-error", "form-success"):
        assert f'id="{live_id}"' in html
    assert 'id="form-error" role="alert"' in html
    assert 'id="form-success" role="status"' in html
    assert 'id="onboarding-form"' in html
    assert 'type="submit"' in html
    assert "Gemeinde anmelden" in html
    assert "fetch('/gemeinde/register'" in html


def test_onboarding_uses_host_canonical():
    client = _make_client()
    resp = client.get("/gemeinde/onboarding", headers={"Host": "openleg.ch"})
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert 'rel="canonical" href="http://openleg.ch/gemeinde/onboarding"' in html


def test_register_accepts_any_known_bfs(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(
        municipality_module.db,
        "get_municipality_profile",
        lambda bfs: {
            "bfs_number": bfs,
            "name": "Dietikon",
            "kanton": "ZH",
            "population": 29000,
        },
    )
    monkeypatch.setattr(
        municipality_module.security_utils,
        "validate_email_address",
        lambda email: (True, email.strip().lower(), ""),
    )
    monkeypatch.setattr(municipality_module.db, "save_municipality", lambda **kwargs: 1)
    monkeypatch.setattr(
        municipality_module.db,
        "update_municipality_status",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        municipality_module.db, "track_event", lambda *args, **kwargs: True
    )

    resp = client.post(
        "/gemeinde/register",
        json={"bfs_number": 261, "admin_email": "Admin@Dietikon.ch"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["municipality_id"] == 1


def test_register_rejects_unknown_bfs(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(
        municipality_module.db, "get_municipality_profile", lambda _bfs: None
    )
    monkeypatch.setattr(
        municipality_module.security_utils,
        "validate_email_address",
        lambda email: (True, email.strip().lower(), ""),
    )

    resp = client.post(
        "/gemeinde/register",
        json={"bfs_number": 999999, "admin_email": "info@example.ch"},
    )
    assert resp.status_code == 400
    assert "Unbekannte BFS-Nummer" in resp.get_json()["error"]


def test_register_rejects_invalid_email(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(
        municipality_module.security_utils,
        "validate_email_address",
        lambda _email: (False, "", "Ungültige E-Mail"),
    )

    resp = client.post(
        "/gemeinde/register",
        json={"bfs_number": 261, "admin_email": "bad-email"},
    )
    assert resp.status_code == 400
    assert "Ungültige E-Mail" in resp.get_json()["error"]


def test_verzeichnis_defaults_to_all_cantons_and_handles_empty(monkeypatch):
    client = _make_client()
    calls = {}

    def _fake_get_all_municipality_profiles(kanton=None, order_by="name"):
        calls["kanton"] = kanton
        calls["order_by"] = order_by
        return []

    monkeypatch.setattr(
        municipality_module.db,
        "get_all_municipality_profiles",
        _fake_get_all_municipality_profiles,
    )
    _patch_verzeichnis_ranking(monkeypatch, [])
    resp = client.get("/gemeinde/verzeichnis")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert "Keine Gemeinden gefunden" in html
    assert calls["kanton"] is None


def test_verzeichnis_shows_score_and_national_rank(monkeypatch):
    client = _make_client()
    profiles = [
        {
            "bfs_number": 1,
            "name": "Sonnenstadt",
            "kanton": "AG",
            "population": 15000,
            "pv_score_pct": 80.0,
        },
        {
            "bfs_number": 2,
            "name": "Schattendorf",
            "kanton": "AG",
            "population": 3000,
            "pv_score_pct": 10.0,
        },
    ]
    monkeypatch.setattr(
        municipality_module.db, "get_all_municipality_profiles", lambda **_k: profiles
    )
    _patch_verzeichnis_ranking(monkeypatch, profiles)
    html = client.get("/gemeinde/verzeichnis?sort=pv_score_pct").data.decode(
        "utf-8", errors="ignore"
    )
    assert "Solarnutzung" in html
    assert "Rang 1 CH" in html
    assert "80%" in html


def test_verzeichnis_and_profil_render_canonical_from_host(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(
        municipality_module.db, "get_all_municipality_profiles", lambda **_kwargs: []
    )
    _patch_verzeichnis_ranking(monkeypatch, [])
    monkeypatch.setattr(
        municipality_module.db,
        "get_municipality_profile",
        lambda bfs: {
            "bfs_number": bfs,
            "name": "Dietikon",
            "kanton": "ZH",
            "energy_transition_score": 0,
        },
    )
    monkeypatch.setattr(
        municipality_module.db, "get_elcom_tariffs", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        municipality_module.db, "get_sonnendach_municipal", lambda _bfs: None
    )

    verzeichnis = client.get("/gemeinde/verzeichnis", headers={"Host": "openleg.ch"})
    assert verzeichnis.status_code == 200
    html_verzeichnis = verzeichnis.data.decode("utf-8", errors="ignore")
    assert (
        'rel="canonical" href="http://openleg.ch/gemeinde/verzeichnis"'
        in html_verzeichnis
    )

    profil = client.get("/gemeinde/profil/261", headers={"Host": "openleg.ch"})
    assert profil.status_code == 200
    html_profil = profil.data.decode("utf-8", errors="ignore")
    assert 'rel="canonical" href="http://openleg.ch/gemeinde/profil/261"' in html_profil


def test_verzeichnis_calls_ranking_load_once(monkeypatch):
    client = _make_client()
    profiles = [
        {
            "bfs_number": 1,
            "name": "Sonnenstadt",
            "kanton": "AG",
            "population": 15000,
            "pv_score_pct": 80.0,
        },
    ]
    monkeypatch.setattr(
        municipality_module.db, "get_all_municipality_profiles", lambda **_k: profiles
    )
    _patch_verzeichnis_ranking(monkeypatch, profiles)

    calls = []

    def _spy_load():
        calls.append(1)
        return municipality_module.Ranking(profiles)

    monkeypatch.setattr(municipality_module.Ranking, "load", _spy_load)
    resp = client.get("/gemeinde/verzeichnis")
    html = resp.data.decode("utf-8", errors="ignore")
    assert resp.status_code == 200
    assert sum(calls) == 1
    assert "Solarnutzung" in html


def _patch_profil_deps(monkeypatch, profile, pv_profiles=None):
    monkeypatch.setattr(
        municipality_module.db, "get_municipality_profile", lambda _bfs: profile
    )
    monkeypatch.setattr(
        municipality_module.db, "get_elcom_tariffs", lambda *_a, **_k: []
    )
    monkeypatch.setattr(
        municipality_module.db, "get_sonnendach_municipal", lambda _bfs: None
    )
    profiles = pv_profiles if pv_profiles is not None else [profile]
    monkeypatch.setattr(
        municipality_module.Ranking,
        "load",
        lambda: municipality_module.Ranking(profiles),
    )


def _patch_verzeichnis_ranking(monkeypatch, profiles):
    monkeypatch.setattr(
        municipality_module.Ranking,
        "load",
        lambda: municipality_module.Ranking(profiles),
    )


def test_profil_solarnutzung_uses_pv_score(monkeypatch):
    client = _make_client()
    _patch_profil_deps(
        monkeypatch,
        {"bfs_number": 4021, "name": "Baden", "kanton": "AG", "pv_score_pct": 42.7},
    )
    resp = client.get("/gemeinde/profil/4021")
    html = resp.data.decode("utf-8", errors="ignore")
    assert "Solarnutzung" in html
    assert "43%" in html


def test_profil_solarnutzung_caps_over_100(monkeypatch):
    client = _make_client()
    _patch_profil_deps(
        monkeypatch,
        {"bfs_number": 1, "name": "Abtwil", "kanton": "AG", "pv_score_pct": 104.0},
    )
    resp = client.get("/gemeinde/profil/1")
    html = resp.data.decode("utf-8", errors="ignore")
    assert "100%" in html
    assert "Schätzung übertroffen" in html


def test_profil_solarnutzung_falls_back_to_old_metric(monkeypatch):
    client = _make_client()
    _patch_profil_deps(
        monkeypatch,
        {
            "bfs_number": 2,
            "name": "Altdorf",
            "kanton": "UR",
            "pv_score_pct": None,
            "solar_potential_pct": 33.0,
        },
    )
    resp = client.get("/gemeinde/profil/2")
    html = resp.data.decode("utf-8", errors="ignore")
    assert "33%" in html


def test_profil_shows_league_chips_and_quality(monkeypatch):
    client = _make_client()
    target = {
        "bfs_number": 4021,
        "name": "Baden",
        "kanton": "AG",
        "population": 19900,
        "density_per_km2": 1500,
        "pv_score_pct": 42.7,
        "pv_plant_match_rate": 76.89,
        "pv_snapshot_year": 2026,
        "pv_untapped_kw": 1000,
    }
    others = [
        target,
        {
            "bfs_number": 2,
            "name": "Sonnendorf",
            "kanton": "AG",
            "population": 3000,
            "density_per_km2": 200,
            "pv_score_pct": 80.0,
        },
    ]
    _patch_profil_deps(monkeypatch, target, pv_profiles=others)
    html = client.get("/gemeinde/profil/4021").data.decode("utf-8", errors="ignore")
    assert "So steht Baden im Vergleich" in html
    assert "Kanton AG" in html
    assert "Datenqualität" in html
    assert "BFE Sonnendach" in html
    assert "Daten melden oder korrigieren" in html
    # Baden ist 2 von 2 in seinem Kanton (niedrigerer Score)
    assert "Rang 2 von 2" in html


def test_profil_shows_improvement_and_leaders(monkeypatch):
    client = _make_client()
    target = {
        "bfs_number": 4021,
        "name": "Baden",
        "kanton": "AG",
        "population": 19900,
        "density_per_km2": 1500,
        "pv_score_pct": 42.7,
        "pv_estimated_potential_kw": 50000.0,
        "pv_installed_kw": 21350.0,
    }
    leader = {
        "bfs_number": 4022,
        "name": "Sonnenstadt",
        "kanton": "AG",
        "population": 15000,
        "density_per_km2": 1200,
        "pv_score_pct": 80.0,
        "pv_estimated_potential_kw": 40000.0,
        "pv_installed_kw": 32000.0,
        "pv_annual_potential_gwh": 6.0,
    }
    _patch_profil_deps(monkeypatch, target, pv_profiles=[target, leader])
    html = client.get("/gemeinde/profil/4021").data.decode("utf-8", errors="ignore")
    assert "Nächster Schritt für Baden" in html
    assert "Ziel:" in html
    assert "kW" in html
    assert "Konkrete Massnahmen" in html
    assert "Solarpflicht" in html
    assert "Vorbilder in AG" in html
    assert "Sonnenstadt" in html


def test_profil_calls_ranking_load_once(monkeypatch):
    client = _make_client()
    target = {
        "bfs_number": 4021,
        "name": "Baden",
        "kanton": "AG",
        "population": 19900,
        "density_per_km2": 1500,
        "pv_score_pct": 42.7,
    }
    others = [target]
    _patch_profil_deps(monkeypatch, target, pv_profiles=others)

    calls = []

    def _spy_load():
        calls.append(1)
        return municipality_module.Ranking(others)

    monkeypatch.setattr(municipality_module.Ranking, "load", _spy_load)
    resp = client.get("/gemeinde/profil/4021")
    html = resp.data.decode("utf-8", errors="ignore")
    assert resp.status_code == 200
    assert sum(calls) == 1
    assert "So steht Baden im Vergleich" in html


def test_profil_skips_ranking_load_without_pv_score(monkeypatch):
    client = _make_client()
    target = {
        "bfs_number": 2,
        "name": "Altdorf",
        "kanton": "UR",
        "pv_score_pct": None,
        "solar_potential_pct": 33.0,
    }
    _patch_profil_deps(monkeypatch, target, pv_profiles=[])

    calls = []

    def _spy_load():
        calls.append(1)
        return municipality_module.Ranking([])

    monkeypatch.setattr(municipality_module.Ranking, "load", _spy_load)
    resp = client.get("/gemeinde/profil/2")
    html = resp.data.decode("utf-8", errors="ignore")
    assert resp.status_code == 200
    assert sum(calls) == 0
    assert "33%" in html


# === SEO: "[Gemeinde] Stromtarif" query targeting (issue #107) ===

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


def _patch_profil_with_tariff(monkeypatch):
    _patch_profil_deps(monkeypatch, dict(BADEN_PROFILE))
    monkeypatch.setattr(
        municipality_module.db,
        "get_elcom_tariffs",
        lambda *_a, **_k: [dict(BADEN_H4_TARIFF)],
    )


def _jsonld_graph(html):
    import json
    import re

    blocks = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', html, flags=re.DOTALL
    )
    assert blocks, "no JSON-LD block on profile page"
    data = json.loads(blocks[0])
    nodes = data.get("@graph", [data])
    return {node.get("@type"): node for node in nodes}


def test_profil_title_and_h1_target_stromtarif_query(monkeypatch):
    client = _make_client()
    _patch_profil_with_tariff(monkeypatch)

    html = client.get("/gemeinde/profil/4021").data.decode("utf-8", errors="ignore")

    title = html.split("<title>")[1].split("</title>")[0]
    assert title.startswith("Stromtarif Baden"), title
    assert "OpenLEG" in title
    h1 = html.split("<h1", 1)[1].split("</h1>")[0]
    assert "Baden" in h1
    assert "Stromtarif" in h1


def test_profil_meta_description_mentions_tariff_value(monkeypatch):
    client = _make_client()
    _patch_profil_with_tariff(monkeypatch)

    html = client.get("/gemeinde/profil/4021").data.decode("utf-8", errors="ignore")

    description = html.split('name="description" content="')[1].split('"')[0]
    assert "Baden" in description
    assert "Stromtarif" in description
    assert "27.5" in description


def test_profil_title_works_without_tariff(monkeypatch):
    client = _make_client()
    _patch_profil_deps(monkeypatch, dict(BADEN_PROFILE))

    html = client.get("/gemeinde/profil/4021").data.decode("utf-8", errors="ignore")

    title = html.split("<title>")[1].split("</title>")[0]
    assert title.startswith("Stromtarif Baden"), title


def test_profil_renders_place_jsonld(monkeypatch):
    client = _make_client()
    _patch_profil_with_tariff(monkeypatch)

    html = client.get(
        "/gemeinde/profil/4021", headers={"Host": "openleg.ch"}
    ).data.decode("utf-8", errors="ignore")

    nodes = _jsonld_graph(html)
    place = nodes.get("Place")
    assert place, f"no Place node in JSON-LD, got {list(nodes)}"
    assert place["name"] == "Baden"
    assert str(place.get("identifier")) == "4021"
    assert "AG" in str(place.get("containedInPlace", ""))

    breadcrumb = nodes.get("BreadcrumbList")
    assert breadcrumb, "no BreadcrumbList node in JSON-LD"
    assert any(
        "verzeichnis" in str(item.get("item", ""))
        for item in breadcrumb["itemListElement"]
    )


def test_profil_jsonld_names_grid_operator(monkeypatch):
    client = _make_client()
    _patch_profil_with_tariff(monkeypatch)

    html = client.get("/gemeinde/profil/4021").data.decode("utf-8", errors="ignore")

    nodes = _jsonld_graph(html)
    operator = nodes.get("Organization")
    assert operator, "no Organization node for the grid operator (VNB)"
    assert operator["name"] == "Regionalwerke AG Baden"


def test_profil_orders_evidence_and_sources_every_figure(monkeypatch):
    client = _make_client()
    _patch_profil_with_tariff(monkeypatch)
    monkeypatch.setattr(
        municipality_module.db,
        "get_sonnendach_municipal",
        lambda _bfs: {
            "total_roof_area_m2": 900000.0,
            "suitable_roof_area_m2": 540000.0,
            "potential_kwp": 81000.0,
            "utilization_pct": 12.4,
        },
    )

    html = client.get("/gemeinde/profil/4021").data.decode("utf-8")

    tiers = [
        html.index('data-evidence-tier="measured"'),
        html.index('data-evidence-tier="derived"'),
        html.index('data-evidence-tier="modelled"'),
    ]
    assert tiers == sorted(tiers)
    assert html.count("data-source-marker=") >= 8
    assert html.count('href="#data-provenance"') >= 8
    assert 'id="data-provenance"' in html
    assert 'data-testid="data-provenance"' in html
    assert 'class="ol-fillbar-track' in html
    assert 'class="ol-fillbar"' in html
    assert "Modellierter Richtwert" in html

    nodes = _jsonld_graph(html)
    assert nodes["Place"]["identifier"] == "4021"
    assert nodes["Organization"]["name"] == "Regionalwerke AG Baden"


def test_profil_jsonld_omits_operator_without_tariff(monkeypatch):
    client = _make_client()
    _patch_profil_deps(monkeypatch, dict(BADEN_PROFILE))

    html = client.get("/gemeinde/profil/4021").data.decode("utf-8", errors="ignore")

    nodes = _jsonld_graph(html)
    assert nodes.get("Place")
    assert "Organization" not in nodes


def test_profil_route_delegates_to_municipality_profile_module(monkeypatch):
    """Route parses request, delegates assembly to municipality_profile, renders (#209)."""
    client = _make_client()
    fake_ctx = {"sentinel": "profile-context"}
    calls = []

    def _fake_profile_context(bfs, *, site_url):
        calls.append({"bfs": bfs, "site_url": site_url})
        return fake_ctx

    monkeypatch.setattr(
        municipality_module.municipality_profile,
        "profile_context",
        _fake_profile_context,
    )

    rendered = {}

    def _fake_render_template(template_name, **context):
        rendered["template"] = template_name
        rendered["context"] = context
        return "ok"

    monkeypatch.setattr(municipality_module, "render_template", _fake_render_template)

    resp = client.get("/gemeinde/profil/4021", headers={"Host": "openleg.ch"})

    assert resp.status_code == 200
    assert calls == [{"bfs": 4021, "site_url": "http://openleg.ch"}]
    assert rendered["template"] == "gemeinde/profil.html"
    assert rendered["context"] == fake_ctx
