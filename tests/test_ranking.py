# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the ranking facade.

Pure unit tests: no Flask app, no real database.
"""

from unittest.mock import patch

import pv_ranking
import ranking
from store import ranking as store_ranking


class TestRanking:
    def test_init_uses_injected_profiles(self):
        profiles = [{"bfs_number": 1, "name": "A"}]
        r = ranking.Ranking(profiles)
        assert r._profiles is profiles

    def test_load_calls_get_pv_profiles_once(self):
        with patch.object(store_ranking, "get_pv_profiles") as mock_get:
            mock_get.return_value = [{"bfs_number": 1, "name": "A"}]
            r = ranking.Ranking.load()
            mock_get.assert_called_once_with()
            assert r._profiles == [{"bfs_number": 1, "name": "A"}]

    def test_load_passes_kanton(self):
        with patch.object(store_ranking, "get_pv_profiles") as mock_get:
            mock_get.return_value = []
            _r = ranking.Ranking.load(kanton="ZH")
            mock_get.assert_called_once_with("ZH")

    def test_national_enriches_and_assigns_ranks(self):
        profiles = [
            {"bfs_number": 1, "name": "A", "pv_score_pct": 120},
            {"bfs_number": 2, "name": "B", "pv_score_pct": 50},
        ]
        r = ranking.Ranking(profiles)
        result = r.national()

        assert len(result) == 2
        assert result[0]["rank"] == 1
        assert result[0]["display_score"] == 100.0
        assert result[0]["score_over_100"] is True
        assert result[1]["rank"] == 2
        assert result[1]["display_score"] == 50.0
        assert result[1]["score_over_100"] is False

    def test_national_empty(self):
        r = ranking.Ranking([])
        assert r.national() == []

    def test_league_chips_delegates(self):
        all_profiles = [
            {
                "bfs_number": 1,
                "name": "A",
                "pv_score_pct": 90,
                "population": 3000,
                "density_per_km2": 100,
                "kanton": "ZH",
            },
            {
                "bfs_number": 2,
                "name": "B",
                "pv_score_pct": 80,
                "population": 3000,
                "density_per_km2": 100,
                "kanton": "ZH",
            },
        ]
        profile = all_profiles[0]
        r = ranking.Ranking(all_profiles)
        chips = r.league_chips(profile)
        expected = pv_ranking.league_standings(all_profiles, profile)
        assert chips == expected


class TestRankingStandings:
    def test_standings_filter_by_kanton(self):
        profiles = [
            {
                "bfs_number": 1,
                "name": "A",
                "pv_score_pct": 120,
                "kanton": "ZH",
                "population": 3000,
                "density_per_km2": 100,
            },
            {
                "bfs_number": 2,
                "name": "B",
                "pv_score_pct": 50,
                "kanton": "BE",
                "population": 3000,
                "density_per_km2": 100,
            },
        ]
        r = ranking.Ranking(profiles)
        result = r.standings(kanton="ZH")

        assert len(result) == 1
        assert result[0]["bfs_number"] == 1
        assert result[0]["rank"] == 1
        assert result[0]["display_score"] == 100.0
        assert result[0]["score_over_100"] is True

    def test_standings_filter_by_size_and_density(self):
        profiles = [
            {
                "bfs_number": 1,
                "name": "A",
                "pv_score_pct": 80,
                "kanton": "ZH",
                "population": 3000,
                "density_per_km2": 100,
            },
            {
                "bfs_number": 2,
                "name": "B",
                "pv_score_pct": 90,
                "kanton": "ZH",
                "population": 30000,
                "density_per_km2": 100,
            },
            {
                "bfs_number": 3,
                "name": "C",
                "pv_score_pct": 70,
                "kanton": "ZH",
                "population": 3000,
                "density_per_km2": 500,
            },
        ]
        r = ranking.Ranking(profiles)
        result = r.standings(size="small", density="low")

        assert len(result) == 1
        assert result[0]["bfs_number"] == 1

    def test_standings_empty(self):
        r = ranking.Ranking([])
        assert r.standings() == []


class TestRankingImprovementTarget:
    def test_improvement_target_with_size_band(self):
        profiles = [
            {
                "bfs_number": 1,
                "name": "A",
                "pv_score_pct": 50,
                "kanton": "ZH",
                "population": 3000,
                "pv_estimated_potential_kw": 1000,
                "pv_installed_kw": 400,
            },
            {
                "bfs_number": 2,
                "name": "B",
                "pv_score_pct": 90,
                "kanton": "ZH",
                "population": 3500,
                "pv_estimated_potential_kw": 1000,
                "pv_installed_kw": 800,
            },
            {
                "bfs_number": 3,
                "name": "C",
                "pv_score_pct": 95,
                "kanton": "ZH",
                "population": 3200,
                "pv_estimated_potential_kw": 1000,
                "pv_installed_kw": 900,
            },
            {
                "bfs_number": 4,
                "name": "D",
                "pv_score_pct": 85,
                "kanton": "ZH",
                "population": 4000,
                "pv_estimated_potential_kw": 1000,
                "pv_installed_kw": 700,
            },
        ]
        profile = profiles[0]
        r = ranking.Ranking(profiles)
        target = r.improvement_target(profile)

        assert target == {
            "target_score": 95,
            "needed_kw": 550.0,
            "roofs": 55,
        }

    def test_improvement_target_no_size_band(self):
        profile = {"bfs_number": 1, "name": "A", "population": None}
        r = ranking.Ranking([profile])
        assert r.improvement_target(profile) is None


class TestRankingLeaders:
    def test_leaders_delegates_to_league_leaders(self):
        profiles = [
            {
                "bfs_number": 1,
                "name": "A",
                "pv_score_pct": 90,
                "kanton": "ZH",
                "population": 3000,
                "pv_annual_potential_gwh": 10,
            },
            {
                "bfs_number": 2,
                "name": "B",
                "pv_score_pct": 80,
                "kanton": "ZH",
                "population": 3000,
                "pv_annual_potential_gwh": 10,
            },
            {
                "bfs_number": 3,
                "name": "C",
                "pv_score_pct": 70,
                "kanton": "BE",
                "population": 3000,
                "pv_annual_potential_gwh": 10,
            },
        ]
        r = ranking.Ranking(profiles)
        with patch.object(pv_ranking, "league_leaders") as mock_leaders:
            mock_leaders.return_value = [{"bfs_number": 2, "name": "B"}]
            result = r.leaders("ZH", exclude_bfs=1)

            assert result == [{"bfs_number": 2, "name": "B"}]
            mock_leaders.assert_called_once()
            args, kwargs = mock_leaders.call_args
            assert all(row["kanton"] == "ZH" for row in args[0])
            assert kwargs.get("exclude_bfs") == 1


class TestRankingMovers:
    def test_movers_calls_store_by_default(self):
        with patch.object(store_ranking, "get_pv_movers") as mock_get:
            mock_get.return_value = [{"bfs_number": 1, "delta": 5.0}]
            r = ranking.Ranking([])
            result = r.movers()

            mock_get.assert_called_once_with()
            assert result == [{"bfs_number": 1, "delta": 5.0}]

    def test_movers_uses_injected_rows(self):
        r = ranking.Ranking([])
        injected = [{"bfs_number": 2, "delta": 3.0}]
        assert r.movers(mover_rows=injected) is injected


class TestRankingBadgeSvg:
    def test_badge_svg_delegates_to_pv_badge(self):
        profiles = [{"bfs_number": 1, "name": "A", "pv_score_pct": 120, "kanton": "ZH"}]
        r = ranking.Ranking(profiles)
        with patch("ranking.pv_badge.badge_svg") as mock_badge:
            mock_badge.return_value = "<svg>badge</svg>"
            result = r.badge_svg(1)

            mock_badge.assert_called_once_with("A", 100.0, 1)
            assert result == "<svg>badge</svg>"

    def test_badge_svg_not_found_returns_empty(self):
        r = ranking.Ranking([])
        assert r.badge_svg(1) == ""
