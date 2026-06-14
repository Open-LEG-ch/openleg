# SPDX-License-Identifier: AGPL-3.0-or-later
"""Municipality profile assembly verb."""

import database as db
import public_data
from ranking import Ranking


def public_profile(bfs: int):
    """Assemble all data for a municipality profile page.

    Returns a dict ready to unpack into render_template, or None if the
    BFS number is unknown.
    """
    profile = db.get_municipality_profile(bfs)
    if not profile:
        return None

    tariffs = db.get_elcom_tariffs(bfs, year=2026)
    solar = db.get_sonnendach_municipal(bfs)

    h4 = next((t for t in tariffs if str(t.get("category", "")).startswith("H4")), None)
    value_gap = public_data.compute_leg_value_gap(h4) if h4 else None

    solar_score, solar_over_100 = Ranking.capped_score(profile.get("pv_score_pct"))
    if solar_score is None and profile.get("solar_potential_pct") is not None:
        solar_score = round(float(profile["solar_potential_pct"]), 1)

    league_chips: list = []
    improvement = None
    already_top = False
    leaders: list = []
    if profile.get("pv_score_pct") is not None:
        r = Ranking.load()
        league_chips = r.league_chips(profile)
        improvement = r.improvement_target(profile)
        size_rank = r.size_league_rank(profile)
        already_top = bool(size_rank and size_rank["quartile"] == Ranking.TOP_QUARTILE)
        leaders = r.leaders(profile.get("kanton"), exclude_bfs=bfs)

    return {
        "profile": profile,
        "tariffs": tariffs,
        "solar": solar,
        "value_gap": value_gap,
        "h4_tariff": h4,
        "solar_score": solar_score,
        "solar_over_100": solar_over_100,
        "league_chips": league_chips,
        "improvement": improvement,
        "already_top": already_top,
        "leaders": leaders,
    }
