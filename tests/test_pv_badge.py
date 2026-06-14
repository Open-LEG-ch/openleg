# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests für SVG-Badges und Social-Cards."""

import os
from unittest.mock import MagicMock

from flask import Flask

import pv_badge
import rangliste as rangliste_module
from ranking import Ranking

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_badge_svg_contains_score_and_rank():
    svg = pv_badge.badge_svg("Baden", 42.0, 12)
    assert svg.startswith("<svg")
    assert "42%" in svg
    assert "Rang 12 CH" in svg
    assert "Baden" in svg


def test_badge_svg_escapes_name():
    svg = pv_badge.badge_svg("A & B <x>", 10.0, None)
    assert "&amp;" in svg
    assert "<x>" not in svg


def test_og_card_svg_dimensions_and_content():
    svg = pv_badge.og_card_svg("Baden", "AG", 42.0, 12, 43750.0)
    assert 'width="1200"' in svg
    assert 'height="630"' in svg
    assert "Baden" in svg
    assert "Kanton AG" in svg
    assert "42%" in svg


def _make_client(monkeypatch, profile, ranking_profiles=None):
    monkeypatch.setattr(
        rangliste_module.db, "get_municipality_profile", lambda _bfs: profile
    )
    monkeypatch.setattr(
        rangliste_module.db,
        "get_pv_profiles",
        lambda kanton=None: (
            [profile] if profile and profile.get("pv_score_pct") is not None else []
        ),
    )

    def _ranking_instance():
        if ranking_profiles is not None:
            return Ranking(ranking_profiles)
        if profile and profile.get("pv_score_pct") is not None:
            return Ranking([profile])
        return Ranking([])

    monkeypatch.setattr(
        rangliste_module.Ranking,
        "load",
        classmethod(lambda cls, kanton=None: _ranking_instance()),
    )
    app = Flask(__name__, template_folder=os.path.join(PROJECT_ROOT, "templates"))
    app.config["TESTING"] = True
    app.register_blueprint(rangliste_module.rangliste_bp)
    return app.test_client()


PROFILE = {
    "bfs_number": 4021,
    "name": "Baden",
    "kanton": "AG",
    "pv_score_pct": 42.0,
    "pv_untapped_kw": 43750.0,
}


def test_badge_route_returns_svg(monkeypatch):
    client = _make_client(monkeypatch, PROFILE)
    resp = client.get("/rangliste/badge/4021.svg")
    assert resp.status_code == 200
    assert resp.mimetype == "image/svg+xml"
    assert b"42%" in resp.data


def test_og_route_returns_svg(monkeypatch):
    client = _make_client(monkeypatch, PROFILE)
    resp = client.get("/rangliste/og/4021.svg")
    assert resp.status_code == 200
    assert resp.mimetype == "image/svg+xml"
    assert b"Baden" in resp.data


NO_PV_PROFILE = {
    "bfs_number": 4021,
    "name": "Baden",
    "kanton": "AG",
    "pv_score_pct": None,
    "pv_untapped_kw": None,
}


def test_badge_route_404_when_missing(monkeypatch):
    client = _make_client(monkeypatch, None)
    assert client.get("/rangliste/badge/9999.svg").status_code == 404


def test_og_route_404_when_missing(monkeypatch):
    client = _make_client(monkeypatch, None)
    assert client.get("/rangliste/og/9999.svg").status_code == 404


def test_badge_route_no_pv_score_returns_na_openleg(monkeypatch):
    client = _make_client(monkeypatch, NO_PV_PROFILE, ranking_profiles=[])
    resp = client.get("/rangliste/badge/4021.svg")
    assert resp.status_code == 200
    data = resp.data.decode("utf-8", errors="ignore")
    assert "n/a" in data
    assert "OpenLEG" in data


def test_og_route_no_pv_score_returns_na_openleg(monkeypatch):
    client = _make_client(monkeypatch, NO_PV_PROFILE, ranking_profiles=[])
    resp = client.get("/rangliste/og/4021.svg")
    assert resp.status_code == 200
    data = resp.data.decode("utf-8", errors="ignore")
    assert "n/a" in data
    assert "Offene Daten von BFE und BFS" in data


def _badge_app_with_mocked_ranking(monkeypatch):
    monkeypatch.setattr(
        rangliste_module.db, "get_municipality_profile", lambda _bfs: PROFILE
    )
    mock_ranking = MagicMock()
    mock_ranking.return_value.badge_svg.return_value = "<svg>ranking-badge</svg>"
    monkeypatch.setattr(rangliste_module.Ranking, "load", mock_ranking)
    app = Flask(__name__, template_folder=os.path.join(PROJECT_ROOT, "templates"))
    app.config["TESTING"] = True
    app.register_blueprint(rangliste_module.rangliste_bp)
    return app.test_client(), mock_ranking


def test_badge_route_delegates_to_ranking_facade(monkeypatch):
    client, mock_ranking = _badge_app_with_mocked_ranking(monkeypatch)
    resp = client.get("/rangliste/badge/4021.svg")
    assert resp.status_code == 200
    assert resp.data == b"<svg>ranking-badge</svg>"
    mock_ranking.assert_called_once_with()
    mock_ranking.return_value.badge_svg.assert_called_once_with(4021, profile=PROFILE)


def _og_app_with_mocked_ranking(monkeypatch):
    monkeypatch.setattr(
        rangliste_module.db, "get_municipality_profile", lambda _bfs: PROFILE
    )
    mock_ranking = MagicMock()
    mock_ranking.return_value.og_card_svg.return_value = "<svg>ranking-og</svg>"
    monkeypatch.setattr(rangliste_module.Ranking, "load", mock_ranking)
    app = Flask(__name__, template_folder=os.path.join(PROJECT_ROOT, "templates"))
    app.config["TESTING"] = True
    app.register_blueprint(rangliste_module.rangliste_bp)
    return app.test_client(), mock_ranking


def test_og_route_delegates_to_ranking_facade(monkeypatch):
    client, mock_ranking = _og_app_with_mocked_ranking(monkeypatch)
    resp = client.get("/rangliste/og/4021.svg")
    assert resp.status_code == 200
    assert resp.data == b"<svg>ranking-og</svg>"
    mock_ranking.assert_called_once_with()
    mock_ranking.return_value.og_card_svg.assert_called_once_with(4021, profile=PROFILE)
