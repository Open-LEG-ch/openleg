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
