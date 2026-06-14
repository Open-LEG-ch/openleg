# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for dashboard.readiness verb (no Flask, no real DB)."""

import dashboard as dash


def _stub_user(monkeypatch, user):
    monkeypatch.setattr(dash.db, "get_building_for_dashboard", lambda _: user)


def _stub_neighbor(monkeypatch, count=0):
    monkeypatch.setattr(dash.db, "get_neighbor_count_near", lambda *_a, **_k: count)


def _stub_referral(monkeypatch, code=None):
    monkeypatch.setattr(dash.db, "get_referral_code", lambda _: code)


def test_returns_error_for_missing_building_id(monkeypatch):
    result = dash.readiness("", city_id=None, app_base_url="http://localhost:5003")
    assert result["error"] is not None
    assert result["user"] is None


def test_returns_error_for_unknown_building_id(monkeypatch):
    monkeypatch.setattr(dash.db, "get_building_for_dashboard", lambda _: None)
    result = dash.readiness(
        "nonexistent", city_id=None, app_base_url="http://localhost:5003"
    )
    assert result["error"] is not None
    assert result["user"] is None


def test_all_checks_true_gives_score_100(monkeypatch):
    user = {
        "building_id": "b1",
        "verified": True,
        "annual_consumption_kwh": 4500,
        "share_with_utility": True,
        "share_with_neighbors": True,
        "lat": 47.37,
        "lon": 8.54,
    }
    _stub_user(monkeypatch, user)
    _stub_neighbor(monkeypatch, 5)
    _stub_referral(monkeypatch, "REF1")
    result = dash.readiness(
        "b1", city_id="zurich", app_base_url="http://localhost:5003"
    )
    assert result["readiness_score"] == 100
    assert result["error"] is None
    assert all(v for _, v in result["checks"])


def test_no_checks_gives_score_0(monkeypatch):
    user = {
        "building_id": "b1",
        "verified": False,
        "annual_consumption_kwh": None,
        "share_with_utility": False,
        "share_with_neighbors": False,
        "lat": None,
        "lon": None,
    }
    _stub_user(monkeypatch, user)
    _stub_neighbor(monkeypatch, 0)
    _stub_referral(monkeypatch, None)
    result = dash.readiness("b1", city_id=None, app_base_url="http://localhost:5003")
    assert result["readiness_score"] == 0
    assert result["referral_link"] == ""


def test_referral_link_built_from_code(monkeypatch):
    user = {"building_id": "b1", "verified": False, "lat": 47.0, "lon": 8.0}
    _stub_user(monkeypatch, user)
    _stub_neighbor(monkeypatch)
    _stub_referral(monkeypatch, "MYCODE")
    result = dash.readiness("b1", city_id=None, app_base_url="http://localhost:5003")
    assert result["referral_link"] == "http://localhost:5003/?ref=MYCODE"


def test_neighbor_count_from_db(monkeypatch):
    user = {"building_id": "b1", "verified": True, "lat": 47.0, "lon": 8.0}
    _stub_user(monkeypatch, user)
    _stub_neighbor(monkeypatch, 7)
    _stub_referral(monkeypatch)
    result = dash.readiness(
        "b1", city_id="zurich", app_base_url="http://localhost:5003"
    )
    assert result["neighbor_count"] == 7


def test_checks_structure(monkeypatch):
    user = {
        "building_id": "b1",
        "verified": True,
        "annual_consumption_kwh": None,
        "share_with_utility": True,
        "share_with_neighbors": False,
        "lat": None,
        "lon": None,
    }
    _stub_user(monkeypatch, user)
    _stub_neighbor(monkeypatch)
    _stub_referral(monkeypatch)
    result = dash.readiness("b1", city_id=None, app_base_url="http://localhost:5003")
    check_map = dict(result["checks"])
    assert check_map["E-Mail bestätigt"] is True
    assert check_map["Verbrauchsdaten hinterlegt"] is False
    assert check_map["EVU-Einwilligung erteilt"] is True
    assert check_map["Nachbar-Einwilligung erteilt"] is False
    assert result["readiness_score"] == 50
