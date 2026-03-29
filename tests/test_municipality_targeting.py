"""Tests for rank_municipalities_for_outreach in insights_engine.py.

Covers US-003: incorporate verified demand into municipality targeting.
"""
import pytest
from insights_engine import rank_municipalities_for_outreach


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _profile(bfs, name, energy_transition_score, leg_value_gap_chf, kanton="ZH"):
    return {
        "bfs_number": bfs,
        "name": name,
        "kanton": kanton,
        "energy_transition_score": energy_transition_score,
        "leg_value_gap_chf": leg_value_gap_chf,
    }


def _demand_signals(*entries):
    """Build a demand_signals dict from (bfs, demand_score, demand_level) triples."""
    signals = []
    for bfs, score, level in entries:
        signals.append({
            "bfs_number": bfs,
            "verified_demand": {"demand_score": score},
            "demand_level": level,
        })
    return {"signals": signals}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRankMunicipalitiesForOutreach:

    def test_higher_demand_elevates_lower_heuristic_municipality(self):
        """A municipality with a higher verified demand score should rank above
        one with better heuristics but zero demand."""
        profiles = [
            _profile(261, "Dietikon", energy_transition_score=60, leg_value_gap_chf=200),
            _profile(247, "Schlieren", energy_transition_score=50, leg_value_gap_chf=150),
        ]
        # Schlieren has much higher demand; Dietikon has zero demand.
        demand = _demand_signals(
            (261, 0, "none"),
            (247, 80, "high"),
        )

        result = rank_municipalities_for_outreach(profiles, demand)

        assert len(result) == 2
        # Schlieren (high demand) should rank first despite lower heuristics.
        assert result[0]["name"] == "Schlieren"
        assert result[1]["name"] == "Dietikon"
        assert result[0]["outreach_score"] > result[1]["outreach_score"]

    def test_demand_score_included_in_outreach_score(self):
        """demand component must be reflected in score_breakdown and outreach_score."""
        profiles = [_profile(261, "Dietikon", energy_transition_score=40, leg_value_gap_chf=100)]
        demand = _demand_signals((261, 60, "medium"))

        result = rank_municipalities_for_outreach(profiles, demand)

        entry = result[0]
        assert entry["demand_score"] == 60
        assert entry["demand_level"] == "medium"
        # Default weight for demand = 0.35 → contribution = 0.35 * 60 = 21
        assert entry["score_breakdown"]["demand"] == pytest.approx(21.0)
        # outreach_score = energy_contrib + gap_contrib + demand_contrib
        assert entry["outreach_score"] == pytest.approx(
            entry["score_breakdown"]["energy_transition"]
            + entry["score_breakdown"]["value_gap"]
            + entry["score_breakdown"]["demand"]
        )

    def test_zero_demand_uses_heuristics_only(self):
        """When no demand signal exists for a municipality the demand contribution
        is zero and ranking is driven by heuristic scores alone."""
        profiles = [
            _profile(261, "Dietikon", energy_transition_score=80, leg_value_gap_chf=300),
            _profile(247, "Schlieren", energy_transition_score=40, leg_value_gap_chf=100),
        ]
        demand = _demand_signals()  # no demand data

        result = rank_municipalities_for_outreach(profiles, demand)

        assert result[0]["name"] == "Dietikon"
        assert result[0]["score_breakdown"]["demand"] == 0.0
        assert result[1]["name"] == "Schlieren"

    def test_custom_weights_change_ranking(self):
        """When demand weight is dominant, demand should drive rank even against
        a municipality with much better heuristic scores."""
        profiles = [
            _profile(261, "Dietikon", energy_transition_score=90, leg_value_gap_chf=400),
            _profile(247, "Schlieren", energy_transition_score=10, leg_value_gap_chf=50),
        ]
        # Schlieren has very high demand; Dietikon has none.
        demand = _demand_signals(
            (261, 0, "none"),
            (247, 100, "high"),
        )
        # Put almost all weight on demand.
        result = rank_municipalities_for_outreach(
            profiles, demand,
            weights={"energy_transition": 0.05, "value_gap": 0.05, "demand": 0.90},
        )

        assert result[0]["name"] == "Schlieren"

    def test_all_municipalities_ranked_highest_first(self):
        """Result list is sorted descending by outreach_score."""
        profiles = [
            _profile(261, "Dietikon", energy_transition_score=50, leg_value_gap_chf=100),
            _profile(247, "Schlieren", energy_transition_score=70, leg_value_gap_chf=200),
            _profile(242, "Urdorf", energy_transition_score=30, leg_value_gap_chf=50),
        ]
        demand = _demand_signals()

        result = rank_municipalities_for_outreach(profiles, demand)

        scores = [r["outreach_score"] for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_value_gap_normalised_to_hundred(self):
        """A leg_value_gap_chf of 500+ should contribute at most the full
        value_gap weight (i.e. value_gap_norm capped at 100)."""
        profiles = [_profile(261, "Dietikon", energy_transition_score=0, leg_value_gap_chf=1000)]
        demand = _demand_signals()

        result = rank_municipalities_for_outreach(profiles, demand)

        entry = result[0]
        # value_gap weight = 0.30, norm capped at 100 → contribution = 30
        assert entry["score_breakdown"]["value_gap"] == pytest.approx(30.0)

    def test_empty_profiles_returns_empty_list(self):
        """No profiles → empty ranked list."""
        result = rank_municipalities_for_outreach([], _demand_signals())
        assert result == []

    def test_output_keys_present(self):
        """Each ranked entry must contain all documented output keys."""
        profiles = [_profile(261, "Dietikon", energy_transition_score=42, leg_value_gap_chf=171)]
        demand = _demand_signals((261, 31, "medium"))

        result = rank_municipalities_for_outreach(profiles, demand)

        assert len(result) == 1
        entry = result[0]
        for key in ("bfs_number", "name", "kanton", "energy_transition_score",
                    "leg_value_gap_chf", "demand_score", "demand_level",
                    "outreach_score", "score_breakdown"):
            assert key in entry, f"Missing key: {key}"
        for sub in ("energy_transition", "value_gap", "demand"):
            assert sub in entry["score_breakdown"], f"Missing breakdown key: {sub}"
