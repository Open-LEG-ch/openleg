# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the honest LEG pre-check (/leg-check).

Contract: /leg-check resolves a municipality from locally cached public data
and shows what is honestly knowable (ElCom grid operator, solar score,
existing registry entries). It never claims grid-topology eligibility; the
honesty boundary is pinned here as hard assertions.
"""

import os
from unittest.mock import MagicMock

from flask import Flask

import leg_registry as leg_registry_module

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BADEN_PROFILE = {
    "bfs_number": 4021,
    "name": "Baden",
    "kanton": "AG",
    "pv_score_pct": 42.0,
}

REGISTRY_ENTRY = {
    "id": 1,
    "slug": "leg-baden",
    "name": "LEG Baden",
    "ort": "Baden",
    "kanton": "AG",
    "moderation_status": "published",
}


def _check_client(monkeypatch, profiles=None, tariffs=None, entries=None):
    monkeypatch.setattr(
        leg_registry_module.db,
        "search_municipality_profiles",
        MagicMock(return_value=profiles if profiles is not None else []),
    )
    monkeypatch.setattr(
        leg_registry_module.db,
        "get_elcom_tariffs",
        MagicMock(return_value=tariffs if tariffs is not None else []),
    )
    monkeypatch.setattr(
        leg_registry_module.db,
        "list_registry_entries",
        MagicMock(return_value=entries if entries is not None else []),
    )
    app = Flask(__name__, template_folder=os.path.join(PROJECT_ROOT, "templates"))
    app.config["TESTING"] = True
    app.register_blueprint(leg_registry_module.registry_bp)
    return app.test_client()


def test_leg_check_form_renders_with_honesty_boundary(monkeypatch):
    client = _check_client(monkeypatch)
    resp = client.get("/leg-check")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert "prüft keine Netz-Topologie" in html


def test_leg_check_result_shows_operator_solar_and_registry(monkeypatch):
    client = _check_client(
        monkeypatch,
        profiles=[BADEN_PROFILE],
        tariffs=[{"operator_name": "Regionalwerke Baden AG"}],
        entries=[REGISTRY_ENTRY],
    )
    resp = client.get("/leg-check?q=Baden")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert "Regionalwerke Baden AG" in html
    assert "LEG Baden" in html
    # the honesty boundary must be present on the RESULT view, not just the form
    assert "prüft keine Netz-Topologie" in html
    assert "Netzbetreiber" in html
    # funnels onward
    assert 'href="/leg-gruenden"' in html
    assert 'href="/leg-verzeichnis/eintragen"' in html


def test_leg_check_result_never_claims_eligibility(monkeypatch):
    client = _check_client(
        monkeypatch,
        profiles=[BADEN_PROFILE],
        tariffs=[{"operator_name": "Regionalwerke Baden AG"}],
        entries=[],
    )
    html = client.get("/leg-check?q=Baden").data.decode("utf-8", errors="ignore")
    for forbidden in ("geeignet für eine LEG", "Eignung bestätigt", "berechtigt"):
        assert forbidden not in html


def test_leg_check_multiple_matches_shows_disambiguation(monkeypatch):
    second = {**BADEN_PROFILE, "bfs_number": 261, "name": "Badenweiler"}
    client = _check_client(monkeypatch, profiles=[BADEN_PROFILE, second])
    html = client.get("/leg-check?q=Bade").data.decode("utf-8", errors="ignore")
    assert "Baden" in html
    assert "Badenweiler" in html


def test_fuer_bewohner_funnels_to_leg_check():
    path = os.path.join(PROJECT_ROOT, "templates", "fuer_bewohner.html")
    with open(path, encoding="utf-8") as handle:
        html = handle.read()
    assert 'href="/leg-check"' in html


def test_leg_verzeichnis_liste_funnels_to_leg_check():
    path = os.path.join(PROJECT_ROOT, "templates", "leg_verzeichnis", "liste.html")
    with open(path, encoding="utf-8") as handle:
        html = handle.read()
    assert 'href="/leg-check"' in html


def test_leg_check_no_match_shows_empty_state(monkeypatch):
    client = _check_client(monkeypatch, profiles=[])
    resp = client.get("/leg-check?q=Nirgendwo")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert "keine gemeinde" in html.lower() or "nicht gefunden" in html.lower()
