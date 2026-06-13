# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests für den Ranglisten-Hub."""

import os

from flask import Flask

import rangliste as rangliste_module

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SAMPLE = [
    {
        "bfs_number": 1,
        "name": "Sonnendorf",
        "kanton": "AG",
        "population": 3000,
        "density_per_km2": 200,
        "pv_score_pct": 80.0,
        "pv_untapped_kw": 1000,
        "pv_annual_potential_gwh": 6.0,
    },
    {
        "bfs_number": 2,
        "name": "Mittelstadt",
        "kanton": "AG",
        "population": 30000,
        "density_per_km2": 1500,
        "pv_score_pct": 40.0,
        "pv_untapped_kw": 50000,
        "pv_annual_potential_gwh": 60.0,
    },
    {
        "bfs_number": 3,
        "name": "Zürichberg",
        "kanton": "ZH",
        "population": 12000,
        "density_per_km2": 900,
        "pv_score_pct": 10.0,
        "pv_untapped_kw": 30000,
        "pv_annual_potential_gwh": 40.0,
    },
    {
        "bfs_number": 4,
        "name": "Übererfüllt",
        "kanton": "ZH",
        "population": 1000,
        "density_per_km2": 100,
        "pv_score_pct": 104.0,
        "pv_untapped_kw": 0,
        "pv_annual_potential_gwh": 2.0,
    },
]


def _make_client(monkeypatch, rows=SAMPLE):
    monkeypatch.setattr(
        rangliste_module.db, "get_pv_profiles", lambda kanton=None: rows
    )
    app = Flask(__name__, template_folder=os.path.join(PROJECT_ROOT, "templates"))
    app.config["TESTING"] = True
    app.register_blueprint(rangliste_module.rangliste_bp)
    return app.test_client()


def test_hub_renders_ranked_table(monkeypatch):
    client = _make_client(monkeypatch)
    resp = client.get("/rangliste")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert "Solarnutzungs-Rangliste" in html
    assert "Sonnendorf" in html
    assert "Vorbild" in html
    assert "Grosse Chance" in html
    # Höchster Score zuerst
    assert (
        html.index("Übererfüllt") < html.index("Sonnendorf") < html.index("Zürichberg")
    )


def test_hub_caps_score_over_100(monkeypatch):
    client = _make_client(monkeypatch)
    html = client.get("/rangliste").data.decode("utf-8", errors="ignore")
    assert "100.0%" in html
    assert "104" not in html


def test_hub_filters_by_canton(monkeypatch):
    client = _make_client(monkeypatch)
    html = client.get("/rangliste?kanton=AG").data.decode("utf-8", errors="ignore")
    assert "Sonnendorf" in html
    assert "Zürichberg" not in html


def test_hub_filters_by_size(monkeypatch):
    client = _make_client(monkeypatch)
    html = client.get("/rangliste?size=large").data.decode("utf-8", errors="ignore")
    assert "Mittelstadt" in html
    assert "Sonnendorf" not in html
