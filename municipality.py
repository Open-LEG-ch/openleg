# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Municipality onboarding for OpenLEG platform.
Handles Gemeinde signup, admin dashboard, LEG formation KPIs.
Public profile pages and directory for municipalities.
"""

import logging
import os
from flask import Blueprint, request, jsonify, render_template, abort

from cantons import SWISS_CANTON_OPTIONS, SWISS_CANTONS
import database as db
import municipality_profile
import pv_data
from ranking import Ranking
import security_utils

logger = logging.getLogger(__name__)

municipality_bp = Blueprint("municipality", __name__, url_prefix="/gemeinde")
pilot_bp = Blueprint("pilot", __name__, url_prefix="/pilotgemeinde")

PILOT_MUNICIPALITIES = municipality_profile.PILOT_MUNICIPALITIES


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


ONBOARDING_STATUS_LABELS = {
    "pending": "In Prüfung",
    "active": "Aktiv",
    "verified": "Verifiziert",
}


def _dashboard_context(muni):
    subdomain = (muni.get("subdomain") or "").strip()
    stats = db.get_stats(city_id=subdomain or None) or {}
    profile = None
    if muni.get("bfs_number"):
        try:
            profile = db.get_municipality_profile(int(muni["bfs_number"]))
        except Exception:
            logger.warning(
                "municipality profile load failed for bfs=%s",
                muni.get("bfs_number"),
                exc_info=True,
            )
            profile = None
    profile = profile or {}
    if subdomain:
        invite_url = f"https://{subdomain}.openleg.ch"
    else:
        invite_url = os.getenv("APP_BASE_URL", "https://openleg.ch").rstrip("/")
    return {
        "municipality": muni,
        "status_label": ONBOARDING_STATUS_LABELS.get(
            muni.get("onboarding_status"), "In Prüfung"
        ),
        "stats": stats,
        "solar_score": profile.get("pv_score_pct"),
        "energy_score": profile.get("energy_transition_score"),
        "invite_url": invite_url,
        "error": None,
    }


@municipality_bp.route("/dashboard")
def dashboard():
    subdomain = request.args.get("subdomain", "").strip()
    bfs = request.args.get("bfs", "")

    muni = None
    if subdomain:
        muni = db.get_municipality(subdomain=subdomain)
    elif bfs:
        try:
            muni = db.get_municipality(bfs_number=int(bfs))
        except ValueError:
            muni = None

    if not muni:
        return render_template(
            "gemeinde/dashboard.html",
            municipality=None,
            error="Gemeinde nicht gefunden.",
        )

    return render_template("gemeinde/dashboard.html", **_dashboard_context(muni))


@municipality_bp.route("/dashboard/demo")
def dashboard_demo():
    """Fake, click-through municipality dashboard for demos and screenshots."""
    return render_template(
        "gemeinde/dashboard.html",
        municipality={
            "name": "Baden",
            "bfs_number": 4021,
            "subdomain": "baden",
            "dso_name": "Regionalwerke AG Baden",
            "onboarding_status": "active",
        },
        status_label="Aktiv",
        stats={"total_buildings": 42, "registrations_today": 3},
        solar_score=34,
        energy_score=61,
        invite_url="https://baden.openleg.ch",
        error=None,
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
    ctx = municipality_profile.profile_context(
        bfs, site_url=request.url_root.rstrip("/")
    )
    if ctx is None:
        abort(404)

    return render_template("gemeinde/profil.html", **ctx)


@pilot_bp.route("/<slug>")
def pilot_case_study(slug):
    """Data-driven trust page for selected pilot municipalities."""
    ctx = municipality_profile.pilot_context(
        slug, site_url=request.url_root.rstrip("/")
    )
    if ctx is None:
        abort(404)

    return render_template("gemeinde/pilotgemeinde.html", **ctx)


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
        data_vintage=pv_data.SNAPSHOT_YEAR,
        plant_match_rate=pv_data.PLANT_MATCH_RATE_PCT,
    )


def _normalize_kanton_param(raw_value):
    raw = (raw_value or "all").strip().upper()
    if raw in ("", "ALL"):
        return None, "all"
    if raw in SWISS_CANTONS:
        return raw, raw
    return None, "all"
