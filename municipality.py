# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Municipality onboarding for OpenLEG platform.
Handles Gemeinde signup, admin dashboard, LEG formation KPIs.
Public profile pages and directory for municipalities.
"""

import logging
from flask import Blueprint, request, jsonify, render_template, abort

from cantons import SWISS_CANTON_OPTIONS, SWISS_CANTONS
import database as db
from ranking import Ranking
import security_utils

logger = logging.getLogger(__name__)

municipality_bp = Blueprint("municipality", __name__, url_prefix="/gemeinde")


def _format_rp_kwh(value):
    if value is None:
        return None
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return None


def _profile_seo(name, h4_tariff):
    h4_total = _format_rp_kwh(h4_tariff.get("total_rp_kwh")) if h4_tariff else None
    year = (h4_tariff or {}).get("year")
    year_part = f" {year}" if year else ""

    if h4_total:
        title = (
            f"Stromtarif {name}{year_part}: {h4_total} Rp/kWh, Solar und LEG | OpenLEG"
        )
        description = (
            f"Stromtarif {name}: {h4_total} Rp/kWh im H4-Profil. "
            "OpenLEG zeigt Solarnutzung und LEG-Potenzial für die Gemeinde."
        )
    else:
        title = f"Stromtarif {name}: Solar und LEG | OpenLEG"
        description = (
            f"Stromtarif {name}: OpenLEG zeigt Solarnutzung, "
            "Energieprofil und LEG-Potenzial für die Gemeinde."
        )

    return title, description


def _profile_jsonld(profile, bfs, h4_tariff, site_url, canonical_url):
    name = (profile.get("name") or "").strip()
    kanton = (profile.get("kanton") or "").strip().upper()[:2]
    graph = [
        {
            "@type": "Place",
            "name": name,
            "identifier": str(bfs),
            "containedInPlace": {
                "@type": "AdministrativeArea",
                "name": kanton,
            },
            "url": canonical_url,
        },
        {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": 1,
                    "name": "Gemeindeverzeichnis",
                    "item": f"{site_url}/gemeinde/verzeichnis",
                },
                {
                    "@type": "ListItem",
                    "position": 2,
                    "name": name,
                    "item": canonical_url,
                },
            ],
        },
    ]

    operator_name = str((h4_tariff or {}).get("operator_name") or "").strip()
    if operator_name:
        graph.append(
            {
                "@type": "Organization",
                "name": operator_name,
                "description": "Verteilnetzbetreiber",
            }
        )

    return {"@context": "https://schema.org", "@graph": graph}


@municipality_bp.route("/onboarding")
def onboarding():
    return render_template(
        "gemeinde/onboarding.html",
        site_url=request.url_root.rstrip("/"),
        canonical_path="/gemeinde/onboarding",
    )


@municipality_bp.route("/register", methods=["POST"])
def register():
    data = request.json or {}
    bfs = data.get("bfs_number")
    admin_email = data.get("admin_email", "").strip()

    if not bfs or not admin_email:
        return jsonify({"error": "BFS-Nummer und E-Mail erforderlich."}), 400

    is_valid, normalized, error = security_utils.validate_email_address(admin_email)
    if not is_valid:
        return jsonify({"error": error}), 400

    profile = db.get_municipality_profile(int(bfs))
    if not profile:
        return jsonify({"error": "Unbekannte BFS-Nummer."}), 400

    name = (profile.get("name") or "").strip()
    kanton = (profile.get("kanton") or "").strip().upper()[:2]
    population = profile.get("population")
    subdomain = (
        name.lower()
        .replace(" ", "-")
        .replace("ü", "ue")
        .replace("ä", "ae")
        .replace("ö", "oe")
    )

    muni_id = db.save_municipality(
        bfs_number=int(bfs),
        name=name,
        kanton=kanton or "ZH",
        dso_name=None,
        population=population,
        subdomain=subdomain,
    )

    if muni_id:
        db.update_municipality_status(int(bfs), "registered", admin_email=normalized)
        db.track_event("municipality_registered", data={"bfs": bfs, "name": name})
        return jsonify(
            {"success": True, "municipality_id": muni_id, "subdomain": subdomain}
        )

    return jsonify({"error": "Registrierung fehlgeschlagen."}), 500


@municipality_bp.route("/dashboard")
def dashboard():
    subdomain = request.args.get("subdomain", "").strip()
    bfs = request.args.get("bfs", "")

    muni = None
    if subdomain:
        muni = db.get_municipality(subdomain=subdomain)
    elif bfs:
        muni = db.get_municipality(bfs_number=int(bfs))

    if not muni:
        return render_template(
            "gemeinde/dashboard.html",
            municipality=None,
            error="Gemeinde nicht gefunden.",
        )

    stats = db.get_stats(city_id=muni.get("subdomain"))
    return render_template(
        "gemeinde/dashboard.html", municipality=muni, stats=stats, error=None
    )


@municipality_bp.route("/api/municipalities")
def api_municipalities():
    profiles = db.get_all_municipality_profiles()
    return jsonify(
        {
            "municipalities": [
                {
                    "bfs": p.get("bfs_number"),
                    "name": p.get("name", ""),
                    "population": p.get("population"),
                    "score": float(p.get("energy_transition_score", 0) or 0),
                    "kanton": p.get("kanton", ""),
                }
                for p in profiles
            ]
        }
    )


# === Public Profile Pages ===


@municipality_bp.route("/profil/<int:bfs>")
def profil(bfs):
    """Public municipality profile page with energy data visualization."""
    profile = db.get_municipality_profile(bfs)
    if not profile:
        abort(404)

    tariffs = db.get_elcom_tariffs(bfs, year=2026)
    solar = db.get_sonnendach_municipal(bfs)

    # Compute value gap if H4 tariff available
    import public_data

    h4 = next((t for t in tariffs if str(t.get("category", "")).startswith("H4")), None)
    value_gap = public_data.compute_leg_value_gap(h4) if h4 else None

    # Kanonische Solarnutzung: neuer PV-Score, gedeckelt; sonst Altwert
    solar_score, solar_over_100 = Ranking.capped_score(profile.get("pv_score_pct"))
    if solar_score is None and profile.get("solar_potential_pct") is not None:
        solar_score = round(float(profile["solar_potential_pct"]), 1)

    # Liga-Ränge, Verbesserungsziel und Vorbilder nur bei vorhandenem PV-Score
    league_chips = []
    improvement = None
    already_top = False
    leaders = []
    if profile.get("pv_score_pct") is not None:
        ranking = Ranking.load()
        league_chips = ranking.league_chips(profile)
        improvement = ranking.improvement_target(profile)
        size_rank = ranking.size_league_rank(profile)
        already_top = bool(size_rank and size_rank["quartile"] == Ranking.TOP_QUARTILE)
        leaders = ranking.leaders(profile.get("kanton"), exclude_bfs=bfs)

    name = (profile.get("name") or "").strip()
    site_url = request.url_root.rstrip("/")
    canonical_url = f"{site_url}/gemeinde/profil/{bfs}"
    seo_title, seo_description = _profile_seo(name, h4)
    jsonld = _profile_jsonld(profile, bfs, h4, site_url, canonical_url)

    return render_template(
        "gemeinde/profil.html",
        profile=profile,
        tariffs=tariffs,
        solar=solar,
        value_gap=value_gap,
        h4_tariff=h4,
        solar_score=solar_score,
        solar_over_100=solar_over_100,
        league_chips=league_chips,
        improvement=improvement,
        already_top=already_top,
        leaders=leaders,
        site_url=site_url,
        share_base=site_url,
        canonical_url=canonical_url,
        seo_title=seo_title,
        seo_description=seo_description,
        jsonld=jsonld,
    )


@municipality_bp.route("/verzeichnis")
def verzeichnis():
    """Searchable municipality directory."""
    kanton_filter, kanton = _normalize_kanton_param(request.args.get("kanton"))
    order_by = request.args.get("sort", "energy_transition_score")
    q = request.args.get("q", "").strip()

    profiles = db.get_all_municipality_profiles(kanton=kanton_filter, order_by=order_by)
    # Reverse for descending score/gap
    if order_by in (
        "energy_transition_score",
        "leg_value_gap_chf",
        "population",
        "pv_score_pct",
    ):
        profiles = list(reversed(profiles))

    if q:
        profiles = [
            p for p in profiles if q.lower() in (p.get("name", "") or "").lower()
        ]

    # Nationaler Solarnutzungs-Rang je Gemeinde
    ranking_rows = {r["bfs_number"]: r for r in Ranking.load().national()}
    for profile in profiles:
        row = ranking_rows.get(profile.get("bfs_number"), {})
        profile["pv_rank"] = row.get("rank")
        profile["display_score"] = row.get("display_score")
        profile["score_over_100"] = row.get("score_over_100")

    return render_template(
        "gemeinde/verzeichnis.html",
        profiles=profiles,
        kanton=kanton,
        query=q,
        sort=order_by,
        site_url=request.url_root.rstrip("/"),
        canton_options=SWISS_CANTON_OPTIONS,
        canonical_path="/gemeinde/verzeichnis",
    )


def _normalize_kanton_param(raw_value):
    raw = (raw_value or "all").strip().upper()
    if raw in ("", "ALL"):
        return None, "all"
    if raw in SWISS_CANTONS:
        return raw, raw
    return None, "all"
