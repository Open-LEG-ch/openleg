# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Municipality onboarding for OpenLEG platform.
Handles Gemeinde signup, admin dashboard, LEG formation KPIs.
Public profile pages and directory for municipalities.
"""

import logging
from flask import Blueprint, request, jsonify, render_template, abort

import database as db
import security_utils

logger = logging.getLogger(__name__)

municipality_bp = Blueprint("municipality", __name__, url_prefix="/gemeinde")

SWISS_CANTON_OPTIONS = [
    ("all", "Alle Kantone"),
    ("AG", "Aargau"),
    ("AI", "Appenzell Innerrhoden"),
    ("AR", "Appenzell Ausserrhoden"),
    ("BE", "Bern"),
    ("BL", "Basel-Landschaft"),
    ("BS", "Basel-Stadt"),
    ("FR", "Freiburg"),
    ("GE", "Genf"),
    ("GL", "Glarus"),
    ("GR", "Graubünden"),
    ("JU", "Jura"),
    ("LU", "Luzern"),
    ("NE", "Neuenburg"),
    ("NW", "Nidwalden"),
    ("OW", "Obwalden"),
    ("SG", "St. Gallen"),
    ("SH", "Schaffhausen"),
    ("SO", "Solothurn"),
    ("SZ", "Schwyz"),
    ("TG", "Thurgau"),
    ("TI", "Tessin"),
    ("UR", "Uri"),
    ("VD", "Waadt"),
    ("VS", "Wallis"),
    ("ZG", "Zug"),
    ("ZH", "Zürich"),
]
SWISS_CANTONS = {code for code, _ in SWISS_CANTON_OPTIONS if code != "all"}


@municipality_bp.route("/onboarding")
def onboarding():
    return render_template("gemeinde/onboarding.html")


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

    return render_template(
        "gemeinde/profil.html",
        profile=profile,
        tariffs=tariffs,
        solar=solar,
        value_gap=value_gap,
        h4_tariff=h4,
        canonical_url=f"{request.url_root.rstrip('/')}/gemeinde/profil/{bfs}",
    )


@municipality_bp.route("/verzeichnis")
def verzeichnis():
    """Searchable municipality directory."""
    kanton_filter, kanton = _normalize_kanton_param(request.args.get("kanton"))
    order_by = request.args.get("sort", "energy_transition_score")
    q = request.args.get("q", "").strip()

    profiles = db.get_all_municipality_profiles(kanton=kanton_filter, order_by=order_by)
    # Reverse for descending score/gap
    if order_by in ("energy_transition_score", "leg_value_gap_chf", "population"):
        profiles = list(reversed(profiles))

    if q:
        profiles = [
            p for p in profiles if q.lower() in (p.get("name", "") or "").lower()
        ]

    return render_template(
        "gemeinde/verzeichnis.html",
        profiles=profiles,
        kanton=kanton,
        query=q,
        sort=order_by,
        canton_options=SWISS_CANTON_OPTIONS,
        canonical_url=f"{request.url_root.rstrip('/')}/gemeinde/verzeichnis",
    )


def _normalize_kanton_param(raw_value):
    raw = (raw_value or "all").strip().upper()
    if raw in ("", "ALL"):
        return None, "all"
    if raw in SWISS_CANTONS:
        return raw, raw
    return None, "all"
