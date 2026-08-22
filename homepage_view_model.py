# SPDX-License-Identifier: AGPL-3.0-or-later
"""Public-safe homepage data shared by HTML preview and JSON API."""

import logging

import database as db
from ranking import Ranking

logger = logging.getLogger(__name__)


def _shape_ranking_row(row: dict) -> dict:
    return {
        "rank": row.get("rank"),
        "name": row.get("name"),
        "kanton": row.get("kanton"),
        "bfs_number": row.get("bfs_number"),
        "score": row.get("display_score"),
    }


def ranking_extremes(n: int = 3) -> tuple[list[dict], list[dict], int]:
    """Return public-safe leaders, lowest scorers, and scored row count."""
    try:
        ranked = Ranking.load().national()
    except Exception:
        logger.exception("ranking preview failed")
        return [], [], 0

    scored = [row for row in ranked if row.get("pv_score_pct") is not None]
    total = len(scored)
    if total < 2 * n:
        return [], [], total

    best = [_shape_ranking_row(row) for row in scored[:n]]
    needs_action = [_shape_ranking_row(row) for row in reversed(scored[-n:])]
    return best, needs_action, total


def build_homepage_view_model(
    territory: str, *, referral_code: str | None = None
) -> dict:
    """Build the homepage model; referral data is opt-in for HTML rendering."""
    stats = db.get_stats(city_id=territory) or {}
    best, needs_action, total = ranking_extremes()
    model = {
        "schema_version": 1,
        "stats": {"registered_buildings": stats.get("total_buildings", 0)},
        "ranking": {
            "best": best,
            "needs_action": needs_action,
            "total": total,
        },
    }

    if referral_code is not None:
        referrer = (
            db.get_building_by_referral_code(referral_code) if referral_code else None
        )
        model["referral"] = {
            "code": referral_code,
            "street": (referrer or {}).get("address", "").split(",")[0],
        }

    return model
