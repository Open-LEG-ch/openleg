# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for municipality_profile.public_profile verb."""

import municipality_profile as mp
from ranking import Ranking


BARE_PROFILE = {
    "bfs_number": 4021,
    "name": "Baden",
    "kanton": "AG",
    "pv_score_pct": None,
}

FULL_PROFILE = {
    "bfs_number": 4021,
    "name": "Baden",
    "kanton": "AG",
    "pv_score_pct": 42.7,
    "population": 19900,
    "density_per_km2": 1500,
    "pv_plant_match_rate": 76.89,
    "pv_snapshot_year": 2026,
    "pv_untapped_kw": 1000,
    "pv_estimated_potential_kw": 50000.0,
    "pv_installed_kw": 21350.0,
}


def _patch_db(monkeypatch, profile, tariffs=None, solar=None):
    monkeypatch.setattr(mp.db, "get_municipality_profile", lambda _: profile)
    monkeypatch.setattr(mp.db, "get_elcom_tariffs", lambda *a, **k: tariffs or [])
    monkeypatch.setattr(mp.db, "get_sonnendach_municipal", lambda _: solar)


def _patch_ranking(monkeypatch, profiles):
    monkeypatch.setattr(mp.Ranking, "load", lambda: Ranking(profiles))


def test_returns_none_for_unknown_bfs(monkeypatch):
    monkeypatch.setattr(mp.db, "get_municipality_profile", lambda _: None)
    assert mp.public_profile(9999) is None


def test_returns_view_dict(monkeypatch):
    _patch_db(monkeypatch, BARE_PROFILE)
    _patch_ranking(monkeypatch, [])
    view = mp.public_profile(4021)
    assert isinstance(view, dict)
    for key in (
        "profile",
        "tariffs",
        "solar",
        "value_gap",
        "h4_tariff",
        "solar_score",
        "solar_over_100",
        "league_chips",
        "improvement",
        "already_top",
        "leaders",
    ):
        assert key in view, f"missing key: {key}"


def test_profile_in_view_matches_db(monkeypatch):
    _patch_db(monkeypatch, FULL_PROFILE)
    _patch_ranking(monkeypatch, [FULL_PROFILE])
    view = mp.public_profile(4021)
    assert view["profile"] is FULL_PROFILE


def test_solar_score_from_pv_score(monkeypatch):
    _patch_db(monkeypatch, FULL_PROFILE)
    _patch_ranking(monkeypatch, [FULL_PROFILE])
    view = mp.public_profile(4021)
    assert view["solar_score"] == 42.7
    assert view["solar_over_100"] is False


def test_solar_score_capped_over_100(monkeypatch):
    profile = {**FULL_PROFILE, "pv_score_pct": 104.0}
    _patch_db(monkeypatch, profile)
    _patch_ranking(monkeypatch, [profile])
    view = mp.public_profile(profile["bfs_number"])
    assert view["solar_score"] == 100.0
    assert view["solar_over_100"] is True


def test_solar_score_falls_back_to_old_metric(monkeypatch):
    profile = {**BARE_PROFILE, "pv_score_pct": None, "solar_potential_pct": 33.0}
    _patch_db(monkeypatch, profile)
    _patch_ranking(monkeypatch, [])
    view = mp.public_profile(profile["bfs_number"])
    assert view["solar_score"] == 33.0
    assert view["solar_over_100"] is False


def test_no_ranking_load_when_no_pv_score(monkeypatch):
    profile = {**BARE_PROFILE, "pv_score_pct": None}
    _patch_db(monkeypatch, profile)
    calls = []
    monkeypatch.setattr(mp.Ranking, "load", lambda: calls.append(1) or Ranking([]))
    mp.public_profile(profile["bfs_number"])
    assert calls == []


def test_ranking_load_called_once_with_pv_score(monkeypatch):
    _patch_db(monkeypatch, FULL_PROFILE)
    calls = []
    monkeypatch.setattr(
        mp.Ranking, "load", lambda: calls.append(1) or Ranking([FULL_PROFILE])
    )
    mp.public_profile(4021)
    assert calls == [1]


def test_value_gap_computed_from_h4_tariff(monkeypatch):
    h4 = {"category": "H4", "total": 25.0}
    _patch_db(monkeypatch, BARE_PROFILE, tariffs=[h4])
    _patch_ranking(monkeypatch, [])
    computed = []
    monkeypatch.setattr(
        mp.public_data, "compute_leg_value_gap", lambda t: computed.append(t) or 42.0
    )
    view = mp.public_profile(4021)
    assert view["value_gap"] == 42.0
    assert computed == [h4]


def test_value_gap_none_without_h4(monkeypatch):
    _patch_db(monkeypatch, BARE_PROFILE, tariffs=[{"category": "H2", "total": 20.0}])
    _patch_ranking(monkeypatch, [])
    view = mp.public_profile(4021)
    assert view["value_gap"] is None


def test_leaders_and_league_chips_populated(monkeypatch):
    leader = {
        "bfs_number": 4022,
        "name": "Sonnenstadt",
        "kanton": "AG",
        "population": 15000,
        "density_per_km2": 1200,
        "pv_score_pct": 80.0,
        "pv_annual_potential_gwh": 6.0,
    }
    _patch_db(monkeypatch, FULL_PROFILE)
    _patch_ranking(monkeypatch, [FULL_PROFILE, leader])
    view = mp.public_profile(4021)
    assert isinstance(view["league_chips"], list)
    assert len(view["leaders"]) >= 1
