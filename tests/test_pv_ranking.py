# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests für die Ranglisten-Logik."""

import pv_ranking


def test_size_band_thresholds():
    assert pv_ranking.size_band(4999) == "small"
    assert pv_ranking.size_band(5000) == "medium"
    assert pv_ranking.size_band(19999) == "medium"
    assert pv_ranking.size_band(20000) == "large"
    assert pv_ranking.size_band(100000) == "xl"
    assert pv_ranking.size_band(None) is None


def test_density_band_thresholds():
    assert pv_ranking.density_band(249) == "low"
    assert pv_ranking.density_band(250) == "mid"
    assert pv_ranking.density_band(1000) == "high"
    assert pv_ranking.density_band(3000) == "very_high"


def test_capped_score_flags_over_100():
    value, over = pv_ranking.capped_score(104.0)
    assert value == 100.0 and over is True
    value, over = pv_ranking.capped_score(42.4)
    assert value == 42.4 and over is False
    assert pv_ranking.capped_score(None) == (None, False)


def test_is_leader_eligible():
    assert pv_ranking.is_leader_eligible({"pv_annual_potential_gwh": 5.0}) is True
    assert pv_ranking.is_leader_eligible({"pv_annual_potential_gwh": 4.9}) is False
    assert pv_ranking.is_leader_eligible({}) is False


def test_assign_ranks_orders_and_quartiles():
    rows = [
        {"bfs_number": i, "pv_score_pct": s}
        for i, s in zip(range(1, 9), [5, 40, 10, 30, 20, 35, 15, 25])
    ]
    ranked = pv_ranking.assign_ranks(rows)
    assert ranked[0]["pv_score_pct"] == 40
    assert ranked[0]["rank"] == 1
    assert ranked[0]["quartile"] == 1
    assert ranked[0]["recommendation"] == "vorbild"
    assert ranked[-1]["pv_score_pct"] == 5
    assert ranked[-1]["quartile"] == 4
    assert ranked[-1]["recommendation"] == "grosse_chance"


def test_top_quartile_threshold():
    rows = [{"pv_score_pct": s} for s in [40, 35, 30, 25, 20, 15, 10, 5]]
    # Bestes Viertel: 40, 35 -> Schwelle 35
    assert pv_ranking.top_quartile_threshold(rows) == 35


def test_improvement_target_computes_gap():
    row = {"pv_estimated_potential_kw": 1000.0, "pv_installed_kw": 100.0}
    target = pv_ranking.improvement_target(row, threshold_score=30.0)
    # Ziel 30% von 1000 = 300 kW, installiert 100 -> 200 kW fehlen
    assert target["needed_kw"] == 200.0
    assert target["roofs"] == 20
    assert target["target_score"] == 30.0


def test_improvement_target_zero_when_above_threshold():
    row = {"pv_estimated_potential_kw": 1000.0, "pv_installed_kw": 400.0}
    target = pv_ranking.improvement_target(row, threshold_score=30.0)
    assert target["needed_kw"] == 0.0


def test_league_standings_ranks_in_each_league():
    target = {
        "bfs_number": 1,
        "kanton": "AG",
        "population": 3000,
        "density_per_km2": 200,
        "pv_score_pct": 20.0,
    }
    rows = [
        target,
        {
            "bfs_number": 2,
            "kanton": "AG",
            "population": 3500,
            "density_per_km2": 220,
            "pv_score_pct": 50.0,
        },
        {
            "bfs_number": 3,
            "kanton": "ZH",
            "population": 4000,
            "density_per_km2": 240,
            "pv_score_pct": 10.0,
        },
    ]
    chips = {c["label"]: c for c in pv_ranking.league_standings(rows, target)}
    assert chips["Schweiz"]["rank"] == 2
    assert chips["Schweiz"]["total"] == 3
    assert chips["Kanton AG"]["rank"] == 2
    assert chips["Kanton AG"]["total"] == 2
    assert chips["Kleine Gemeinden"]["total"] == 3
    assert "Ländlich" in chips


def test_league_leaders_excludes_self_and_ineligible():
    rows = [
        {
            "bfs_number": 1,
            "name": "Self",
            "pv_score_pct": 90,
            "pv_annual_potential_gwh": 9,
        },
        {
            "bfs_number": 2,
            "name": "Gross",
            "pv_score_pct": 60,
            "pv_annual_potential_gwh": 9,
        },
        {
            "bfs_number": 3,
            "name": "Dorf",
            "pv_score_pct": 99,
            "pv_annual_potential_gwh": 2,
        },
        {
            "bfs_number": 4,
            "name": "Mittel",
            "pv_score_pct": 70,
            "pv_annual_potential_gwh": 6,
        },
    ]
    leaders = pv_ranking.league_leaders(rows, exclude_bfs=1, n=3)
    names = [leader["name"] for leader in leaders]
    assert names == [
        "Mittel",
        "Gross",
    ]  # Dorf ist nicht zitierfähig, Self ausgeschlossen


def test_filter_league_by_canton_and_size():
    rows = [
        {"kanton": "AG", "population": 3000, "density_per_km2": 500},
        {"kanton": "ZH", "population": 3000, "density_per_km2": 500},
        {"kanton": "AG", "population": 30000, "density_per_km2": 500},
    ]
    out = pv_ranking.filter_league(rows, kanton="ag", size="small")
    assert len(out) == 1
    assert out[0]["kanton"] == "AG"
