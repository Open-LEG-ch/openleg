# SPDX-License-Identifier: AGPL-3.0-or-later
"""Gemeinde-Solarnutzungs-Rangliste.

Öffentliche Liga-Tabellen und Fortschritts-Ansicht. Vergleicht Gemeinden mit
fairen Peers und zeigt das nächste konkrete Ziel.
"""

import logging

from flask import Blueprint, Response, render_template, request

import database as db
import pv_ranking
from cantons import SWISS_CANTON_OPTIONS
from ranking import Ranking

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


def _common_context(kanton, size, density):
    return {
        "kanton": kanton or "",
        "size": size or "",
        "density": density or "",
        "site_url": request.url_root.rstrip("/"),
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

    ranking = Ranking.load()
    rows = ranking.standings(
        kanton=kanton.upper() if kanton else None, size=size, density=density
    )

    context = _common_context(kanton, size, density)
    context.update(
        {
            "rows": rows[:limit],
            "total": len(rows),
            "limit": limit,
            "active_tab": "rangliste",
            "canonical_path": "/rangliste",
        }
    )
    return render_template("gemeinde/rangliste.html", **context)


def _bfs_arg(name):
    raw = (request.args.get(name) or "").strip()
    return int(raw) if raw.isdigit() else None


@rangliste_bp.route("/rangliste/badge/<int:bfs>.svg")
def badge(bfs):
    profile = db.get_municipality_profile(bfs)
    if not profile:
        return Response(status=404)
    ranking = Ranking.load()
    svg = ranking.badge_svg(bfs, profile=profile)
    return Response(svg, mimetype="image/svg+xml")


@rangliste_bp.route("/rangliste/og/<int:bfs>.svg")
def og_card(bfs):
    profile = db.get_municipality_profile(bfs)
    if not profile:
        return Response(status=404)
    ranking = Ranking.load()
    svg = ranking.og_card_svg(bfs, profile=profile)
    return Response(svg, mimetype="image/svg+xml")


@rangliste_bp.route("/rangliste/vergleich")
def vergleich():
    bfs_a = _bfs_arg("a")
    bfs_b = _bfs_arg("b")

    ranking = Ranking.load()
    national = ranking.national()
    rank_map = {r["bfs_number"]: r["rank"] for r in national}

    def enrich(bfs):
        if not bfs:
            return None
        profile = db.get_municipality_profile(bfs)
        if not profile:
            return None
        score, over_100 = pv_ranking.capped_score(profile.get("pv_score_pct"))
        return {
            **profile,
            "display_score": score,
            "score_over_100": over_100,
            "pv_rank": rank_map.get(bfs),
        }

    municipalities = sorted(
        (
            {"bfs_number": r["bfs_number"], "name": r["name"], "kanton": r["kanton"]}
            for r in national
        ),
        key=lambda m: m["name"] or "",
    )

    return render_template(
        "gemeinde/vergleich.html",
        a=enrich(bfs_a),
        b=enrich(bfs_b),
        bfs_a=bfs_a,
        bfs_b=bfs_b,
        municipalities=municipalities,
        site_url=request.url_root.rstrip("/"),
        canonical_path="/rangliste/vergleich",
    )


@rangliste_bp.route("/rangliste/methodik")
def methodik():
    import pv_data

    return render_template(
        "gemeinde/methodik.html",
        plant_match_rate=pv_data.PLANT_MATCH_RATE_PCT,
        site_url=request.url_root.rstrip("/"),
        canonical_path="/rangliste/methodik",
    )


@rangliste_bp.route("/rangliste/fortschritte")
def movers():
    kanton = _clean_param("kanton")
    size = _clean_param("size")
    density = _clean_param("density")
    try:
        limit = max(10, min(int(request.args.get("limit", "100")), 3000))
    except ValueError:
        limit = 100

    ranking = Ranking([])
    rows = ranking.movers()
    league = pv_ranking.filter_league(
        rows, kanton=kanton.upper() if kanton else None, size=size, density=density
    )
    latest_year = league[0]["year"] if league else None

    context = _common_context(kanton, size, density)
    context.update(
        {
            "rows": league[:limit],
            "total": len(league),
            "limit": limit,
            "latest_year": latest_year,
            "active_tab": "fortschritte",
            "canonical_path": "/rangliste/fortschritte",
        }
    )
    return render_template("gemeinde/rangliste_fortschritte.html", **context)
