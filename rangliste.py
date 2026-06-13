# SPDX-License-Identifier: AGPL-3.0-or-later
"""Gemeinde-Solarnutzungs-Rangliste.

Öffentliche Liga-Tabellen und Fortschritts-Ansicht. Vergleicht Gemeinden mit
fairen Peers und zeigt das nächste konkrete Ziel.
"""

import logging

from flask import Blueprint, render_template, request

import database as db
import pv_ranking
from municipality import SWISS_CANTON_OPTIONS

logger = logging.getLogger(__name__)

rangliste_bp = Blueprint("rangliste", __name__)

SIZE_OPTIONS = [
    ("", "Alle Grössen"),
    ("small", "Klein (< 5000)"),
    ("medium", "Mittel (< 20000)"),
    ("large", "Gross (< 100000)"),
    ("xl", "Sehr gross (>= 100000)"),
]
DENSITY_OPTIONS = [
    ("", "Alle Dichten"),
    ("low", "Ländlich (< 250)"),
    ("mid", "Mittel (< 1000)"),
    ("high", "Dicht (< 3000)"),
    ("very_high", "Sehr dicht (>= 3000)"),
]


def _clean_param(name):
    value = (request.args.get(name) or "").strip()
    if not value or value.upper() == "ALL":
        return None
    return value


def _filtered_league(kanton, size, density):
    rows = db.get_pv_profiles()
    return pv_ranking.filter_league(rows, kanton=kanton, size=size, density=density)


def _with_display(ranked):
    """Gedeckelten Score und Überschreitungs-Flag je Zeile ergänzen."""
    enriched = []
    for row in ranked:
        score, over_100 = pv_ranking.capped_score(row.get("pv_score_pct"))
        enriched.append({**row, "display_score": score, "score_over_100": over_100})
    return enriched


def _common_context(kanton, size, density):
    return {
        "kanton": kanton or "",
        "size": size or "",
        "density": density or "",
        "canton_options": SWISS_CANTON_OPTIONS,
        "size_options": SIZE_OPTIONS,
        "density_options": DENSITY_OPTIONS,
    }


@rangliste_bp.route("/rangliste")
def hub():
    kanton = _clean_param("kanton")
    size = _clean_param("size")
    density = _clean_param("density")
    try:
        limit = max(10, min(int(request.args.get("limit", "250")), 3000))
    except ValueError:
        limit = 250

    league = _filtered_league(kanton.upper() if kanton else None, size, density)
    ranked = _with_display(pv_ranking.assign_ranks(league))

    context = _common_context(kanton, size, density)
    context.update(
        {
            "rows": ranked[:limit],
            "total": len(ranked),
            "limit": limit,
            "active_tab": "rangliste",
            "canonical_url": f"{request.url_root.rstrip('/')}/rangliste",
        }
    )
    return render_template("gemeinde/rangliste.html", **context)
