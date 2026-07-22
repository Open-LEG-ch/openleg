# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests für den Ranglisten-Hub."""

import os
from unittest.mock import MagicMock

from flask import Flask

import pv_ranking
import rangliste as rangliste_module
from ranking import Ranking

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


def _make_hub_client(monkeypatch, rows=SAMPLE):
    monkeypatch.setattr(rangliste_module, "Ranking", Ranking, raising=False)
    mock_load = MagicMock(return_value=Ranking(rows))
    monkeypatch.setattr(rangliste_module.Ranking, "load", mock_load)
    app = Flask(__name__, template_folder=os.path.join(PROJECT_ROOT, "templates"))
    app.config["TESTING"] = True
    app.register_blueprint(rangliste_module.rangliste_bp)
    return app.test_client(), mock_load


def test_hub_renders_ranked_table(monkeypatch):
    client, _ = _make_hub_client(monkeypatch)
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
    client, _ = _make_hub_client(monkeypatch)
    html = client.get("/rangliste").data.decode("utf-8", errors="ignore")
    assert "100.0%" in html
    assert "104" not in html


def test_hub_filters_by_canton(monkeypatch):
    client, _ = _make_hub_client(monkeypatch)
    html = client.get("/rangliste?kanton=AG").data.decode("utf-8", errors="ignore")
    assert "Sonnendorf" in html
    assert "Zürichberg" not in html


def test_hub_filters_by_size(monkeypatch):
    client, _ = _make_hub_client(monkeypatch)
    html = client.get("/rangliste?size=large").data.decode("utf-8", errors="ignore")
    assert "Mittelstadt" in html
    assert "Sonnendorf" not in html


def test_hub_calls_ranking_load_once(monkeypatch):
    client, mock_load = _make_hub_client(monkeypatch)
    resp = client.get("/rangliste")
    assert resp.status_code == 200
    mock_load.assert_called_once_with()


MOVERS = [
    {
        "bfs_number": 2,
        "name": "Mittelstadt",
        "kanton": "AG",
        "population": 30000,
        "density_per_km2": 1500,
        "year": 2025,
        "score_now": 40.0,
        "score_prev": 31.5,
        "delta": 8.5,
    },
    {
        "bfs_number": 3,
        "name": "Zürichberg",
        "kanton": "ZH",
        "population": 12000,
        "density_per_km2": 900,
        "year": 2025,
        "score_now": 10.0,
        "score_prev": 9.0,
        "delta": 1.0,
    },
]


def _make_movers_client(monkeypatch, rows=MOVERS):
    mock_ranking = MagicMock()
    instance = mock_ranking.return_value
    instance.movers.side_effect = (
        lambda mover_rows=None, kanton=None, size=None, density=None: pv_ranking.filter_league(
            rows, kanton=kanton, size=size, density=density
        )
    )
    monkeypatch.setattr(rangliste_module, "Ranking", mock_ranking)
    app = Flask(__name__, template_folder=os.path.join(PROJECT_ROOT, "templates"))
    app.config["TESTING"] = True
    app.register_blueprint(rangliste_module.rangliste_bp)
    return app.test_client(), mock_ranking, instance


def test_movers_tab_renders_delta(monkeypatch):
    client, mock_ranking, instance = _make_movers_client(monkeypatch)
    resp = client.get("/rangliste/fortschritte")
    assert resp.status_code == 200
    mock_ranking.assert_called_once_with([])
    instance.movers.assert_called_once_with(kanton=None, size=None, density=None)
    html = resp.data.decode("utf-8", errors="ignore")
    assert "Grösste Fortschritte" in html
    assert "+8.50 Pkt" in html
    assert "2025" in html
    # Reihenfolge bleibt nach Delta absteigend
    assert html.index("Mittelstadt") < html.index("Zürichberg")


def test_movers_tab_filters_by_canton(monkeypatch):
    client, _, _ = _make_movers_client(monkeypatch)
    html = client.get("/rangliste/fortschritte?kanton=ZH").data.decode(
        "utf-8", errors="ignore"
    )
    assert "Zürichberg" in html
    assert "Mittelstadt" not in html


def _make_compare_client(monkeypatch, rows=SAMPLE):
    by_bfs = {r["bfs_number"]: r for r in rows}
    monkeypatch.setattr(
        rangliste_module.db, "get_municipality_profile", lambda bfs: by_bfs.get(bfs)
    )
    mock_load = MagicMock(return_value=Ranking(rows))
    monkeypatch.setattr(rangliste_module.Ranking, "load", mock_load)
    app = Flask(__name__, template_folder=os.path.join(PROJECT_ROOT, "templates"))
    app.config["TESTING"] = True
    app.register_blueprint(rangliste_module.rangliste_bp)
    return app.test_client(), mock_load


def test_vergleich_picker_without_selection(monkeypatch):
    client, _ = _make_compare_client(monkeypatch)
    html = client.get("/rangliste/vergleich").data.decode("utf-8", errors="ignore")
    assert "Gemeinden vergleichen" in html
    assert "Sonnendorf" in html  # im Dropdown


def test_vergleich_shows_two_municipalities(monkeypatch):
    client, _ = _make_compare_client(monkeypatch)
    html = client.get("/rangliste/vergleich?a=1&b=3").data.decode(
        "utf-8", errors="ignore"
    )
    assert "Sonnendorf" in html
    assert "Zürichberg" in html
    assert "Nationaler Rang" in html
    assert "Ungenutztes Potenzial" in html


def test_vergleich_calls_ranking_load_once(monkeypatch):
    client, mock_load = _make_compare_client(monkeypatch)
    resp = client.get("/rangliste/vergleich?a=1&b=3")
    assert resp.status_code == 200
    mock_load.assert_called_once_with()


def test_methodik_page_renders_caveats_and_register(monkeypatch):
    app = Flask(__name__, template_folder=os.path.join(PROJECT_ROOT, "templates"))
    app.config["TESTING"] = True
    app.register_blueprint(rangliste_module.rangliste_bp)
    client = app.test_client()
    resp = client.get("/rangliste/methodik")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="ignore")
    assert "Methodik" in html
    assert "BFE Sonnendach" in html
    assert "ungematcht" in html
    assert "zentrales LEG-Register" in html
