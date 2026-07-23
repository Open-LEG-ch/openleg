# SPDX-License-Identifier: AGPL-3.0-or-later
"""Municipality profile pages report the local LEG picture.

Phase 7 of docs/leg-registry.md: a Gemeinde page shows published entries
from the open registry for that municipality, funnels into /leg-check,
and the municipalities pathway page links the registry.
"""

import os

from flask import Flask

import municipality as municipality_module
import pv_data

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(PROJECT_ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def _make_client():
    app = Flask(__name__, template_folder=os.path.join(PROJECT_ROOT, "templates"))
    app.config["TESTING"] = True
    app.register_blueprint(municipality_module.municipality_bp)
    return app.test_client()


def _patch_verzeichnis(monkeypatch, profiles):
    monkeypatch.setattr(
        municipality_module.db, "get_all_municipality_profiles", lambda **_k: profiles
    )
    monkeypatch.setattr(
        municipality_module.Ranking,
        "load",
        lambda: municipality_module.Ranking(profiles),
    )


VERZEICHNIS_PROFILES = [
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
    {
        "bfs_number": 3,
        "name": "Bergblick",
        "kanton": "GR",
        "population": 5000,
        "pv_score_pct": 50.0,
    },
]


def test_verzeichnis_groups_entries_by_canton(monkeypatch):
    client = _make_client()
    _patch_verzeichnis(monkeypatch, VERZEICHNIS_PROFILES)
    # sort=name avoids the score-based reversal so the mocked order round-trips.
    html = client.get("/gemeinde/verzeichnis?sort=name").data.decode(
        "utf-8", errors="ignore"
    )
    assert 'id="kt-AG"' in html
    assert 'id="kt-GR"' in html
    assert 'href="#kt-AG"' in html
    assert 'href="#kt-GR"' in html
    # index groups keep both AG Gemeinden together
    assert (
        html.index("Sonnenstadt") < html.index("Schattendorf") < html.index("Bergblick")
    )


def test_verzeichnis_shows_data_provenance(monkeypatch):
    client = _make_client()
    _patch_verzeichnis(monkeypatch, VERZEICHNIS_PROFILES)
    html = client.get("/gemeinde/verzeichnis").data.decode("utf-8", errors="ignore")
    assert 'data-testid="data-provenance"' in html
    assert str(pv_data.SNAPSHOT_YEAR) in html


def test_verzeichnis_filters_and_search_still_round_trip(monkeypatch):
    client = _make_client()
    calls = {}

    def _fake(kanton=None, order_by="name"):
        calls["kanton"] = kanton
        calls["order_by"] = order_by
        return VERZEICHNIS_PROFILES

    monkeypatch.setattr(municipality_module.db, "get_all_municipality_profiles", _fake)
    monkeypatch.setattr(
        municipality_module.Ranking,
        "load",
        lambda: municipality_module.Ranking(VERZEICHNIS_PROFILES),
    )
    resp = client.get("/gemeinde/verzeichnis?kanton=AG&sort=name&q=Sonnen")
    assert resp.status_code == 200
    assert calls["kanton"] == "AG"
    assert calls["order_by"] == "name"


def test_profil_route_loads_registry_entries():
    source = _read("municipality_profile.py")
    assert "list_registry_entries" in source


def test_profil_template_has_leg_section():
    html = _read("templates", "gemeinde", "profil.html")
    assert "Lokale Elektrizitätsgemeinschaften" in html
    assert "leg_entries" in html
    assert 'href="/leg-check?q=' in html
    assert 'href="/leg-verzeichnis/eintragen"' in html


def test_fuer_gemeinden_links_registry():
    html = _read("templates", "fuer_gemeinden.html")
    assert 'href="/leg-verzeichnis"' in html
