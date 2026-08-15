# SPDX-License-Identifier: AGPL-3.0-or-later
import hashlib
import logging
import math
import os
import threading
from datetime import timedelta

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from flask import (
    Blueprint,
    Flask,
    Response,
    abort,
    current_app,
    g,
    jsonify,
    render_template,
    request,
    send_from_directory,
)

import billing_runner
import cache as cache_module
import dashboard as dashboard_module  # noqa: F401
import dashboard_access as dashboard_access_module  # noqa: F401
import dashboard_routes
import data_enricher
import database as db
import email_automation
import formation_wizard
import geometry
import leg_registry
import ml_models
import registration
import security_utils
import tenant as tenant_module
from admin import admin_bp, require_admin
from api_public import public_api_bp
from email_utils import send_email
from health import health_bp
from leg_registry import registry_bp
from municipality import PILOT_MUNICIPALITIES, municipality_bp, pilot_bp
from rangliste import rangliste_bp
from ranking import Ranking
from registration import CONSENT_VERSION, parse_consents  # noqa: F401
from security_utils import log_security_event
from self_host import self_host_bp
from utility_portal import utility_bp

# --- Security imports ---
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    from flask_talisman import Talisman

    HAS_SECURITY_LIBS = True
except ImportError:
    HAS_SECURITY_LIBS = False

logger = logging.getLogger(__name__)

# --- App routes ---
main_bp = Blueprint("main", __name__)


@main_bp.app_errorhandler(429)
def handle_rate_limit(_error):
    return (
        jsonify(
            {
                "error": (
                    "Zu viele Anfragen. Bitte warten Sie eine Minute und "
                    "versuchen Sie es erneut."
                )
            }
        ),
        429,
    )


# --- Rate Limiting & Security ---
if HAS_SECURITY_LIBS:
    limiter = Limiter(
        get_remote_address,
        default_limits=["500 per hour"],
        strategy="fixed-window",
    )
else:
    limiter = None


def render_city_template(template_name, **kwargs):
    """Render the canonical template with tenant context."""
    tenant = getattr(g, "tenant", tenant_module.DEFAULT_TENANT)
    kwargs.setdefault("tenant", tenant)
    kwargs.setdefault("site_url", current_app.config["SITE_URL"])
    kwargs.setdefault(
        "ga4_id", tenant.get("ga4_id") or os.getenv("GA4_MEASUREMENT_ID", "")
    )
    return render_template(template_name, **kwargs)


@main_bp.after_app_request
def apply_basic_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    is_private_dashboard = request.path in {"/dashboard", "/dashboard/export"} and bool(
        dashboard_routes._dashboard_session_building_id()
    )
    is_private_leg_dashboard = request.path == "/leg/dashboard" and bool(
        dashboard_routes._dashboard_session_building_id()
    )
    is_private_leg_document = request.path.startswith("/leg/document/")
    if (
        request.path.startswith("/dashboard/access/")
        or is_private_dashboard
        or is_private_leg_dashboard
        or is_private_leg_document
    ):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


# --- Anonymity ---
ANONYMITY_RADIUS_METERS = 120


def jitter_coordinates(lat, lon, radius_meters=ANONYMITY_RADIUS_METERS, seed=None):
    if lat is None or lon is None or radius_meters <= 0:
        return lat, lon
    if seed is not None:
        if not isinstance(seed, str):
            seed = str(seed)
        seed_hash = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        seed_value = int(seed_hash, 16)
    else:
        seed_value = None
    rng = np.random.default_rng(seed_value)
    distance = radius_meters * math.sqrt(rng.random())
    angle = rng.uniform(0, 2 * math.pi)
    earth_radius = 6_378_137.0
    lat_rad = math.radians(lat)
    delta_lat = (distance * math.cos(angle)) / earth_radius
    denom = earth_radius * math.cos(lat_rad)
    if abs(denom) < 1e-9:
        denom = earth_radius
    delta_lon = (distance * math.sin(angle)) / denom
    return lat + math.degrees(delta_lat), lon + math.degrees(delta_lon)


def _tenant_name():
    try:
        return getattr(g, "tenant", {}).get("platform_name", "OpenLEG")
    except RuntimeError:
        return "OpenLEG"


def send_activity_notification(activity_type, details):
    name = _tenant_name()
    subject = f"{name}: {activity_type}"
    message_body = (
        f"Neue Aktivität auf {name}:\n\nTyp: {activity_type}\n\nDetails:\n{details}"
    )
    send_email(current_app.config["ADMIN_EMAIL"], subject, message_body)


def send_confirmation_email(email, unsubscribe_url, building_id=None, address=None):
    name = _tenant_name()
    try:
        city = getattr(g, "tenant", {}).get("city_name", "Zürich")
    except RuntimeError:
        city = "Zürich"
    subject = f"{name}: Registrierung bestätigt"
    message_body = (
        f"Willkommen bei {name}!\n\n"
        f"Sie sind jetzt für eine Lokale Elektrizitätsgemeinschaft (LEG) in {city} registriert.\n\n"
        "Wir informieren Sie per E-Mail, sobald sich neue Interessenten in Ihrer Zone anmelden.\n\n"
        f"Abmelden:\n{unsubscribe_url}\n\n"
        f"Ihr {name}-Team"
    )
    send_email(email, subject, message_body)


def collect_building_locations(city_id=None, exclude_building_id=None):
    """Get all verified building locations with jittered coordinates."""
    buildings = db.get_all_buildings(city_id=city_id)
    locations = []
    for b in buildings:
        if exclude_building_id and b.get("building_id") == exclude_building_id:
            continue
        lat = b.get("lat")
        lon = b.get("lon")
        if lat is None or lon is None:
            continue
        jlat, jlon = jitter_coordinates(
            float(lat), float(lon), seed=b.get("building_id")
        )
        locations.append(
            {"lat": jlat, "lon": jlon, "type": b.get("user_type", "anonymous")}
        )
    return locations


def run_full_ml_task(new_building_id=None, city_id=None):
    """Background ML clustering task using PostgreSQL data."""
    logger.info("[ML] Starting background clustering...")
    profiles = db.get_all_building_profiles(city_id=city_id)
    if len(profiles) < 2:
        logger.info("[ML] Not enough buildings for clustering.")
        return

    building_data = pd.DataFrame(profiles)
    ranked_communities, buildings_with_clusters = ml_models.find_optimal_communities(
        building_data, radius_meters=150, min_community_size=2
    )

    # Save clusters to DB
    if "building_id" in buildings_with_clusters.columns:
        for _, row in buildings_with_clusters.iterrows():
            bid = row.get("building_id")
            cid = row.get("cluster", -1)
            if bid and cid >= 0:
                db.save_cluster(bid, cid)

    for community in ranked_communities:
        db.save_cluster_info(community["community_id"], community)

    logger.info(f"[ML] Clustering done: {len(ranked_communities)} clusters")


def find_provisional_matches(new_profile):
    """Fast provisional match search (distance only, no DBSCAN)."""
    profiles = db.get_all_building_profiles()
    if not profiles:
        return None

    new_coords = (new_profile["lat"], new_profile["lon"])
    provisional = [new_profile]

    for p in profiles:
        dist = ml_models.calculate_distance(
            new_coords[0], new_coords[1], float(p["lat"]), float(p["lon"])
        )
        if dist <= 150:
            provisional.append(p)

    if len(provisional) < 2:
        return None

    community_df = pd.DataFrame(provisional)
    autarky_score, _, _ = ml_models.calculate_community_autarky(community_df, None)

    members = [
        {
            "building_id": p.get("building_id", ""),
            "lat": float(p["lat"]),
            "lon": float(p["lon"]),
        }
        for p in provisional
    ]
    return {
        "community_id": "provisional",
        "num_members": len(members),
        "members": members,
        "autarky_percent": autarky_score * 100,
    }


def create_simple_polygon(coords):
    if len(coords) < 3:
        if len(coords) == 1:
            lat, lon = coords[0]
            o = 0.0005
            return [
                [lat - o, lon - o],
                [lat + o, lon - o],
                [lat + o, lon + o],
                [lat - o, lon + o],
                [lat - o, lon - o],
            ]
        elif len(coords) == 2:
            lat1, lon1 = coords[0]
            lat2, lon2 = coords[1]
            o = 0.0003
            return [
                [lat1 - o, lon1 - o],
                [lat2 + o, lon1 - o],
                [lat2 + o, lon2 + o],
                [lat1 - o, lon2 + o],
                [lat1 - o, lon1 - o],
            ]
    hull = geometry.convex_hull(coords)
    if hull is not None:
        return hull + [hull[0]]
    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    o = 0.0003
    return [
        [min(lats) - o, min(lons) - o],
        [max(lats) + o, min(lons) - o],
        [max(lats) + o, max(lons) + o],
        [min(lats) - o, max(lons) + o],
        [min(lats) - o, min(lons) - o],
    ]


# ===========================
# Routes
# ===========================


def _ranking_extremes(n=3):
    """Top und Schluss der Solarnutzungs-Rangliste für die Startseiten-Vorschau.

    Gibt (vorbilder, chancen, total) zurück. Leer, wenn zu wenige Daten.
    """
    try:
        ranked = Ranking.load().national()
    except Exception:
        logger.exception("ranking preview failed")
        return [], [], 0
    scored = [r for r in ranked if r.get("pv_score_pct") is not None]
    total = len(scored)
    if total < 2 * n:
        return [], [], total

    def shape(row):
        return {
            "rank": row.get("rank"),
            "name": row.get("name"),
            "kanton": row.get("kanton"),
            "bfs_number": row.get("bfs_number"),
            "score": row.get("display_score"),
        }

    best = [shape(r) for r in scored[:n]]
    worst = [shape(r) for r in reversed(scored[-n:])]
    return best, worst, total


@main_bp.route("/")
def index():
    city_id = g.tenant.get("territory", "zurich") if hasattr(g, "tenant") else "zurich"
    stats = db.get_stats(city_id=city_id)
    user_count = stats.get("total_buildings", 0)
    referral_code = request.args.get("ref", "")
    referrer_info = None
    if referral_code:
        referrer_info = db.get_building_by_referral_code(referral_code)
    ranking_best, ranking_worst, ranking_total = _ranking_extremes()
    return render_city_template(
        "index.html",
        user_count=user_count,
        referral_code=referral_code,
        ranking_best=ranking_best,
        ranking_worst=ranking_worst,
        ranking_total=ranking_total,
        referrer_street=referrer_info.get("address", "").split(",")[0]
        if referrer_info
        else "",
    )


@main_bp.route("/how-it-works")
def how_it_works():
    return render_city_template("how-it-works.html")


@main_bp.route("/fuer-bewohner")
def fuer_bewohner():
    return render_city_template("fuer_bewohner.html")


@main_bp.route("/fuer-gemeinden")
def fuer_gemeinden():
    return render_city_template("fuer_gemeinden.html")


@main_bp.route("/open-source")
def open_source():
    return render_city_template("open_source.html")


@main_bp.route("/leg-gruenden")
def leg_gruenden():
    return render_city_template("leg_gruenden.html")


@main_bp.route("/leg-kalkulator")
def leg_kalkulator():
    return render_city_template("leg_kalkulator.html")


@main_bp.route("/pricing")
def pricing():
    return render_city_template("pricing.html")


@main_bp.route("/robots.txt")
def robots_txt():
    lines = [
        "User-agent: *",
        "Allow: /",
        "Allow: /api/v1/docs",
        "Disallow: /api/",
        "Disallow: /admin/",
        "Disallow: /confirm/",
        "Disallow: /unsubscribe/",
        f"Sitemap: {current_app.config['SITE_URL']}/sitemap.xml",
    ]
    return Response("\n".join(lines) + "\n", mimetype="text/plain")


@main_bp.route("/favicon.ico")
def favicon():
    return send_from_directory(
        current_app.static_folder,
        "favicon.ico",
        mimetype="image/vnd.microsoft.icon",
    )


@main_bp.route("/sitemap.xml")
def sitemap_xml():
    """Render and briefly cache the public sitemap for the current site and day."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    current_date = datetime.now(ZoneInfo("Europe/Zurich")).strftime("%Y-%m-%d")
    cache_key = f"sitemap:{current_app.config['SITE_URL']}:{current_date}"
    cached_xml = cache_module.cache_get(cache_key)
    if cached_xml is not None:
        return Response(cached_xml, mimetype="application/xml")
    pages = [
        ("/", "1.0", "daily", current_date),
        ("/how-it-works", "0.8", "weekly", current_date),
        ("/fuer-bewohner", "0.9", "weekly", current_date),
        ("/fuer-gemeinden", "0.8", "weekly", current_date),
        ("/leg-gruenden", "0.9", "weekly", current_date),
        ("/leg-kalkulator", "0.9", "weekly", current_date),
        ("/pricing", "0.7", "monthly", current_date),
        ("/open-source", "0.8", "weekly", current_date),
        ("/self-host", "0.8", "weekly", current_date),
        ("/gemeinde/verzeichnis", "0.9", "weekly", current_date),
        ("/leg-verzeichnis", "0.9", "weekly", current_date),
        ("/leg-check", "0.9", "weekly", current_date),
        ("/rangliste", "0.9", "daily", current_date),
        ("/rangliste/fortschritte", "0.8", "daily", current_date),
        ("/rangliste/vergleich", "0.7", "weekly", current_date),
        ("/rangliste/methodik", "0.6", "monthly", current_date),
        ("/api/v1/docs", "0.8", "weekly", current_date),
        ("/gemeinde/onboarding", "0.9", "weekly", current_date),
        ("/impressum", "0.3", "yearly", "2026-01-01"),
        ("/datenschutz", "0.3", "yearly", "2026-01-01"),
    ]
    for bfs in db.get_all_municipality_profile_bfs_numbers():
        pages.append((f"/gemeinde/profil/{bfs}", "0.8", "weekly", current_date))
    for slug in PILOT_MUNICIPALITIES:
        pages.append((f"/pilotgemeinde/{slug}", "0.8", "weekly", current_date))
    xml = render_template(
        "sitemap.xml", site_url=current_app.config["SITE_URL"], pages=pages
    )
    cache_module.cache_set(cache_key, xml, ttl=3600)
    return Response(xml, mimetype="application/xml")


## Health endpoints registered via health_bp


# --- Address API ---
@main_bp.route("/api/suggest_addresses")
@limiter.limit("30 per minute") if limiter else lambda f: f
def api_suggest_addresses():
    query = request.args.get("q", "").strip()
    query = security_utils.sanitize_string(query, max_length=100)
    if not query or len(query) < 2:
        return jsonify({"suggestions": []})
    limit = 15 if len(query) < 5 else 10
    plz_ranges = g.tenant.get("plz_ranges") if hasattr(g, "tenant") else None
    suggestions_raw = data_enricher.get_address_suggestions(
        query, limit=limit, plz_ranges=plz_ranges
    )
    if suggestions_raw is None:
        return jsonify({"error": "Adressvorschläge sind derzeit nicht verfügbar."}), 503
    suggestions = []
    for s in suggestions_raw:
        if isinstance(s, dict) and s.get("label") and s.get("label").strip():
            label = security_utils.sanitize_string(s.get("label", ""), max_length=200)
            if label:
                suggestions.append(
                    {
                        "label": label,
                        "lat": s.get("lat"),
                        "lon": s.get("lon"),
                        "plz": s.get("plz"),
                    }
                )
    return jsonify({"suggestions": suggestions})


@main_bp.route("/api/get_all_buildings")
def api_get_all_buildings():
    city_id = g.tenant.get("territory") if hasattr(g, "tenant") else None
    locations = collect_building_locations(city_id=city_id)
    return jsonify({"buildings": locations})


@main_bp.route("/api/get_all_clusters")
def api_get_all_clusters():
    """Return cluster geometry from one bulk-loaded database result."""
    clusters_raw = db.get_all_clusters()
    clusters = []
    for ci in clusters_raw:
        members = ci.get("members", [])
        if not members or len(members) < 2:
            continue
        coords = []
        member_list = []
        for member in members:
            if not isinstance(member, dict):
                continue
            building_id = member.get("building_id")
            lat = member.get("lat")
            lon = member.get("lon")
            if building_id and lat is not None and lon is not None:
                coords.append([float(lat), float(lon)])
                member_list.append(
                    {
                        "building_id": building_id,
                        "lat": float(lat),
                        "lon": float(lon),
                    }
                )
        if len(coords) >= 2:
            clusters.append(
                {
                    "cluster_id": ci.get("cluster_id"),
                    "members": member_list,
                    "polygon": create_simple_polygon(coords),
                    "autarky_percent": float(ci.get("autarky_percent", 0)),
                    "num_members": len(member_list),
                }
            )
    return jsonify({"clusters": clusters})


# --- Check Potential ---
@main_bp.route("/api/check_potential", methods=["POST"])
@limiter.limit("10 per minute") if limiter else lambda f: f
def api_check_potential():
    try:
        is_valid_size, size_error = security_utils.check_request_size(request)
        if not is_valid_size:
            return jsonify({"error": size_error}), 413
        if not request.json:
            return jsonify({"error": "Keine Daten empfangen."}), 400
        address = request.json.get("address", "").strip()
        is_valid, sanitized_address, error_msg = security_utils.validate_address(
            address
        )
        if not is_valid:
            return jsonify({"error": error_msg}), 400
        address = sanitized_address

        estimates, _profiles = None, None
        try:
            estimates, _profiles = data_enricher.get_energy_profile_for_address(address)
            if not estimates:
                estimates, _profiles = (
                    data_enricher.get_mock_energy_profile_for_address(address)
                )
        except Exception:
            estimates, _profiles = data_enricher.get_mock_energy_profile_for_address(
                address
            )

        if not estimates:
            return jsonify({"error": "Adresse konnte nicht analysiert werden."}), 404

        cluster_info = find_provisional_matches(estimates)
        if not cluster_info:
            return jsonify(
                {
                    "potential": False,
                    "message": "Keine direkten Partner gefunden.",
                    "profile_summary": estimates,
                }
            )
        return jsonify(
            {
                "potential": True,
                "message": "Partner gefunden!",
                "cluster_info": cluster_info,
                "profile_summary": estimates,
            }
        )
    except Exception:
        current_app.logger.exception("Unhandled error in /api/check_potential")
        return jsonify({"error": "Server-Fehler. Bitte später erneut versuchen."}), 500


# --- Registration ---
def _registration_response(user_type):
    if not request.json:
        return jsonify({"error": "Keine Daten empfangen."}), 400
    is_valid_size, size_error = security_utils.check_request_size(request)
    if not is_valid_size:
        return jsonify({"error": size_error}), 413

    city_id = g.tenant.get("territory", "zurich") if hasattr(g, "tenant") else "zurich"
    deps = registration.RegistrationDeps(
        db=db,
        security=security_utils,
        app_base_url=current_app.config["APP_BASE_URL"],
        thread=threading.Thread,
        send_confirmation_email=send_confirmation_email,
        run_full_ml_task=run_full_ml_task,
        schedule_sequence_for_user=email_automation.schedule_sequence_for_user,
        find_provisional_matches=find_provisional_matches,
        collect_building_locations=collect_building_locations,
    )
    try:
        payload = registration.register(
            request.json, city_id=city_id, user_type=user_type, deps=deps
        )
    except registration.RegistrationError as error:
        return jsonify({"error": error.message}), error.status
    return jsonify(payload)


@main_bp.route("/api/register_anonymous", methods=["POST"])
@limiter.limit("5 per minute") if limiter else lambda f: f
def api_register_anonymous():
    return _registration_response("anonymous")


@main_bp.route("/api/register_full", methods=["POST"])
@limiter.limit("5 per minute") if limiter else lambda f: f
def api_register_full():
    return _registration_response("registered")


# --- Meter Data Upload ---
@main_bp.route("/api/meter-data/upload", methods=["POST"])
@limiter.limit("10 per minute") if limiter else lambda f: f
def api_meter_data_upload():
    import meter_data

    data = request.json or {}
    building_id = data.get("building_id", "").strip()
    csv_content = data.get("csv_content", "")

    try:
        tier = int(data.get("tier", 1))
        if tier not in (1, 2, 3):
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "tier muss 1, 2 oder 3 sein."}), 400

    if not building_id or not csv_content:
        return jsonify({"error": "building_id und csv_content erforderlich."}), 400

    try:
        # Verify building exists
        building = db.get_building(building_id)
        if not building:
            return jsonify({"error": "Gebäude nicht gefunden."}), 404

        # Save consent tier
        db.save_data_consent(
            building_id,
            tier=tier,
            share_municipality=True,
            share_research=(tier >= 2),
            share_providers=(tier >= 3),
        )

        return jsonify(meter_data.ingest_file(building_id, csv_content))
    except Exception:
        current_app.logger.exception("Unhandled error in /api/meter-data/upload")
        return jsonify({"error": "Server-Fehler beim Verarbeiten der Messdaten."}), 500


@main_bp.route("/meter-upload")
def meter_upload_page():
    return render_city_template("meter_upload.html")


# --- Unsubscribe ---
@main_bp.route("/impressum")
def impressum():
    return render_city_template("impressum.html")


@main_bp.route("/datenschutz")
def datenschutz():
    return render_city_template("datenschutz.html")


@main_bp.route("/unsubscribe", methods=["GET", "POST"])
@limiter.limit("5 per minute") if limiter else lambda f: f
def unsubscribe_page():
    status = None
    message = None
    email_value = ""

    if request.method == "POST":
        email_value = (request.form.get("email") or "").strip()
        is_valid_email, normalized_email, email_error = (
            security_utils.validate_email_address(email_value)
        )
        if not is_valid_email:
            status = "error"
            message = email_error
        else:
            email_value = normalized_email
            matches = db.get_building_by_email(email_value)
            if matches:
                for m in matches:
                    token = security_utils.generate_uuid()
                    saved = db.save_token(
                        token, m["building_id"], "unsubscribe", ttl_seconds=3600
                    )
                    if not saved:
                        continue
                    unsubscribe_url = f"{current_app.config['APP_BASE_URL'].rstrip('/')}/unsubscribe/{token}"
                    try:
                        send_email(
                            email_value,
                            "OpenLEG: Löschung bestätigen",
                            "Bestätigen Sie die Löschung Ihrer OpenLEG-Daten über "
                            f"diesen Link:\n\n{unsubscribe_url}\n\n"
                            "Der Link ist eine Stunde gültig. Falls Sie die Löschung "
                            "nicht angefordert haben, ignorieren Sie diese E-Mail.",
                        )
                    except Exception:
                        current_app.logger.exception(
                            "Failed to send profile deletion confirmation"
                        )
            email_value = ""
            status = "success"
            message = (
                "Falls ein Eintrag vorhanden ist, erhalten Sie einen Bestätigungslink "
                "per E-Mail."
            )

    return render_city_template(
        "unsubscribe.html", status=status, message=message, email=email_value
    )


@main_bp.route("/unsubscribe/<token>", methods=["GET", "POST"])
@limiter.limit("10 per minute") if limiter else lambda f: f
def unsubscribe_token(token):
    try:
        token_uuid = security_utils.validate_uuid(token)
    except ValueError:
        abort(404)

    token_info = db.get_token(token_uuid)
    if not token_info or token_info.get("token_type") != "unsubscribe":
        abort(404)

    if request.method == "GET":
        return render_template(
            "unsubscribe.html",
            status=None,
            message=None,
            email="",
            confirm_deletion=True,
        )

    if not db.confirm_profile_deletion(token_uuid):
        return (
            render_template(
                "unsubscribe.html",
                status="error",
                message=(
                    "Ihre Daten wurden nicht gelöscht. Der Link ist möglicherweise "
                    "abgelaufen. Fordern Sie einen neuen Bestätigungslink an."
                ),
                email="",
            ),
            409,
        )
    return render_template(
        "unsubscribe.html",
        status="success",
        message="Ihre Daten wurden erfolgreich gelöscht.",
        email="",
    )


# --- Dashboard ---
# Dashboard and LEG HTTP surface is registered from dashboard_routes so app.py
# stays within its line budget.
def _dashboard_send_email(*args, **kwargs):
    return send_email(*args, **kwargs)


dashboard_routes.register_dashboard_routes(
    main_bp,
    send_email=_dashboard_send_email,
    limiter=limiter,
    render_city_template=render_city_template,
)


# --- Referral System ---
@main_bp.route("/api/referral/stats/<building_id>")
def api_referral_stats(building_id):
    stats = db.get_referral_stats(building_id)
    referral_code = db.get_referral_code(building_id)
    return jsonify(
        {
            "referral_code": referral_code,
            "referral_link": (
                f"{current_app.config['APP_BASE_URL']}/?ref={referral_code}"
            )
            if referral_code
            else None,
            "total_referrals": stats.get("total_referrals", 0),
        }
    )


@main_bp.route("/api/referral/leaderboard")
def api_referral_leaderboard():
    city_id = g.tenant.get("territory") if hasattr(g, "tenant") else None
    leaderboard = db.get_referral_leaderboard(limit=10, city_id=city_id)
    for entry in leaderboard:
        street = entry.get("street", "")
        entry["display_name"] = street[:15] + "..." if len(street) > 15 else street
    return jsonify({"leaderboard": leaderboard})


@main_bp.route("/api/stats/public")
def api_public_stats():
    city_id = g.tenant.get("territory") if hasattr(g, "tenant") else None
    stats = db.get_stats(city_id=city_id)
    return jsonify(
        {
            "total_users": stats.get("total_buildings", 0),
            "registrations_today": stats.get("registrations_today", 0),
        }
    )


@main_bp.route("/api/stats/live")
def api_live_stats():
    city_id = g.tenant.get("territory", "zurich") if hasattr(g, "tenant") else None
    stats = db.get_stats(city_id=city_id)
    return jsonify(
        {
            "total_registered": stats.get("total_buildings", 0),
            "last_24h": stats.get("registrations_today", 0),
            "clusters_ready": 0,
            "avg_savings_chf": 520,
        }
    )


# --- Savings Calculator ---
@main_bp.route("/api/calculate_savings", methods=["POST"])
def api_calculate_savings():
    data = request.json or {}
    consumption = float(data.get("consumption_kwh", 4500))
    has_solar = bool(data.get("has_solar", False))
    pv_kwp = float(data.get("pv_kwp", 0))
    tenant = getattr(g, "tenant", {})
    solar_yield = tenant.get(
        "solar_kwh_per_kwp", formation_wizard.DEFAULT_SOLAR_KWH_PER_KWP
    )
    result = formation_wizard.calculate_savings_estimate(
        consumption_kwh=consumption,
        pv_kwp=pv_kwp if has_solar else 0,
        community_size=5,
        solar_kwh_per_kwp=solar_yield,
    )
    return jsonify(result)


# --- Formation API ---
@main_bp.route("/api/formation/optimize", methods=["POST"])
def api_formation_optimize():
    """LEG optimization endpoint."""
    data = request.json or {}
    building_id = data.get("building_id", "").strip()
    if not building_id:
        return jsonify({"error": "building_id required"}), 400

    clusters = formation_wizard.get_formable_clusters(db, building_id)
    return jsonify({"clusters": clusters})


@main_bp.route("/api/formation/financial-model", methods=["POST"])
def api_formation_financial_model():
    """Savings projection for a LEG."""
    data = request.json or {}
    consumption = float(data.get("consumption_kwh", 4500))
    pv_kwp = float(data.get("pv_kwp", 0))
    community_size = int(data.get("community_size", 5))
    solar_kwh = (
        g.tenant.get("solar_kwh_per_kwp", formation_wizard.DEFAULT_SOLAR_KWH_PER_KWP)
        if hasattr(g, "tenant")
        else formation_wizard.DEFAULT_SOLAR_KWH_PER_KWP
    )

    result = formation_wizard.calculate_savings_estimate(
        consumption, pv_kwp, community_size, solar_kwh
    )
    return jsonify(result)


# --- Cron ---
def _require_cron_secret():
    """Cron endpoints fail closed: no CRON_SECRET configured means no access."""
    secret = request.headers.get("X-Cron-Secret") or request.args.get("secret") or ""
    configured = current_app.config["CRON_SECRET"]
    if not configured or secret != configured:
        log_security_event("CRON_ACCESS_DENIED", "Invalid cron secret", "WARNING")
        abort(403)


@main_bp.route("/api/cron/process-emails", methods=["POST"])
def api_cron_process_emails():
    _require_cron_secret()
    result = email_automation.process_email_queue(app=current_app)
    return jsonify(result)


@main_bp.route("/api/cron/refresh-public-data", methods=["POST"])
def api_cron_refresh_public_data():
    _require_cron_secret()
    import public_data

    result = public_data.refresh_canton("ZH")
    return jsonify(result)


@main_bp.route("/api/cron/backfill-elcom", methods=["POST"])
def api_cron_backfill_elcom():
    _require_cron_secret()
    import public_data

    year = request.args.get("year", 2026, type=int)
    limit = request.args.get("limit", 25, type=int) or 25
    safe_limit = max(1, min(limit, 200))
    bfs_numbers = db.get_profile_bfs_missing_elcom_tariffs(year=year, limit=safe_limit)

    result = {
        "year": year,
        "limit": safe_limit,
        "candidates": len(bfs_numbers),
        "processed": 0,
        "saved": 0,
        "errors": [],
    }
    for bfs in bfs_numbers:
        result["processed"] += 1
        try:
            tariffs = public_data.fetch_elcom_tariffs(bfs, year=year)
            if tariffs:
                result["saved"] += int(db.save_elcom_tariffs(tariffs) or 0)
        except Exception:
            logger.exception("ElCom backfill failed for BFS %s", bfs)
            result["errors"].append({"bfs": bfs, "error": "fetch_failed"})
    return jsonify(result)


@main_bp.route("/api/email/stats")
def api_email_stats():
    require_admin()
    return jsonify(db.get_email_stats())


# --- Webhooks ---


@main_bp.route("/webhook/deepsign", methods=["POST"])
def webhook_deepsign():
    """Handle DeepSign e-signature webhook callbacks."""
    import deepsign_integration

    signature = request.headers.get("X-DeepSign-Signature", "")
    if not deepsign_integration.verify_webhook_signature(request.get_data(), signature):
        log_security_event("DEEPSIGN_WEBHOOK_DENIED", "Invalid signature", "WARNING")
        abort(403)
    payload = request.get_json(silent=True) or {}
    result = deepsign_integration.handle_webhook(payload)
    logger.info(
        f"[DEEPSIGN] Webhook: {result.get('action')} for {result.get('document_id')}"
    )
    return jsonify(result), 200


# --- Billing Cron ---
@main_bp.route("/api/cron/process-billing", methods=["POST"])
def api_cron_process_billing():
    _require_cron_secret()

    communities = db.get_active_communities()
    period_start, period_end = billing_runner.previous_complete_month()
    processed = 0
    already_processed = 0
    failures = []
    for community in communities:
        community_id = community["community_id"]
        try:
            result = billing_runner.run_billing_period(
                community_id, period_start, period_end
            )
        except billing_runner.BillingRunError:
            logger.error("Billing run failed for community %s", community_id)
            failures.append(
                {"community_id": community_id, "error": "billing_run_failed"}
            )
            continue
        if result["status"] == "created":
            processed += 1
        elif result["status"] == "already_processed":
            already_processed += 1
    return jsonify(
        {
            "activated": True,
            "status": "ok" if not failures else "partial_failure",
            "processed": processed,
            "already_processed": already_processed,
            "failed": len(failures),
            "failures": failures,
            "communities": len(communities),
        }
    )


@main_bp.route("/api/cron/verify-registry-entries", methods=["POST"])
def api_cron_verify_registry_entries():
    _require_cron_secret()

    result = leg_registry.send_verification_nudges(
        base_url=current_app.config["SITE_URL"]
    )
    return jsonify(result)


@main_bp.route("/api/billing/community/<community_id>/period/<int:period_id>")
def api_billing_period(community_id, period_id):
    require_admin()
    period = db.get_billing_period(period_id)
    if not period:
        return jsonify({"error": "Period not found"}), 404
    return jsonify(period)


# --- Metrics ---
@main_bp.route("/metrics")
def metrics():
    stats = db.get_stats()
    communities = db.get_active_communities()
    return jsonify(
        {
            "active_communities": len(communities),
            "total_buildings": stats.get("total_buildings", 0),
            "registrations_today": stats.get("registrations_today", 0),
        }
    )


def _parse_dashboard_ttl_seconds(raw, default):
    """Parse a dashboard token TTL env value with safe bounds."""
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid dashboard TTL value %r, using default %s", raw, default)
        return default
    return max(60, min(value, 86_400))


def create_app(config=None, *, load_environment=True, check_database=True):
    """Create one configured OpenLEG Flask application."""
    if load_environment:
        load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()],
    )
    if check_database and not db.is_db_available():
        raise RuntimeError("PostgreSQL required. Set DATABASE_URL.")

    application = Flask(__name__)
    app_base_url = os.getenv("APP_BASE_URL", "http://localhost:5003")
    secure_cookie_env = os.getenv("SESSION_COOKIE_SECURE")
    default_session_cookie_secure = app_base_url.startswith("https://")
    if secure_cookie_env is not None:
        default_session_cookie_secure = secure_cookie_env.strip().lower() in {
            "true",
            "1",
            "yes",
            "on",
        }
    application.config.from_mapping(
        JSON_SORT_KEYS=False,
        SECRET_KEY=os.getenv("SECRET_KEY", os.urandom(32).hex()),
        SESSION_COOKIE_SECURE=default_session_cookie_secure,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE=os.getenv("SESSION_COOKIE_SAMESITE", "Lax"),
        PERMANENT_SESSION_LIFETIME=timedelta(
            seconds=int(os.getenv("PERMANENT_SESSION_LIFETIME", "3600"))
        ),
        DASHBOARD_ACCESS_TOKEN_TTL_SECONDS=_parse_dashboard_ttl_seconds(
            os.getenv("DASHBOARD_ACCESS_TOKEN_TTL_SECONDS"), 900
        ),
        DASHBOARD_EMAIL_TOKEN_TTL_SECONDS=_parse_dashboard_ttl_seconds(
            os.getenv("DASHBOARD_EMAIL_TOKEN_TTL_SECONDS"), 86_400
        ),
        MAX_CONTENT_LENGTH=10 * 1024 * 1024,
        APP_BASE_URL=app_base_url,
        SITE_URL=app_base_url.rstrip("/"),
        ALLOWED_HOSTS=os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(","),
        ADMIN_EMAIL=os.getenv("ADMIN_EMAIL", "hallo@openleg.ch"),
        CRON_SECRET=os.getenv("CRON_SECRET", "").strip(),
        RATELIMIT_STORAGE_URI=os.getenv("REDIS_URL", "redis://redis:6379/1"),
    )
    if config:
        application.config.update(config)
        if "APP_BASE_URL" in config and "SITE_URL" not in config:
            application.config["SITE_URL"] = config["APP_BASE_URL"].rstrip("/")
        if (
            "SESSION_COOKIE_SECURE" not in config
            and secure_cookie_env is None
            and application.config["APP_BASE_URL"].startswith("https://")
        ):
            application.config["SESSION_COOKIE_SECURE"] = True

    for blueprint in (
        main_bp,
        municipality_bp,
        pilot_bp,
        public_api_bp,
        health_bp,
        utility_bp,
        rangliste_bp,
        registry_bp,
        self_host_bp,
        admin_bp,
    ):
        application.register_blueprint(blueprint)
    tenant_module.init_tenant_middleware(application, db=db)

    if HAS_SECURITY_LIBS:
        limiter.init_app(application)
        Talisman(
            application,
            force_https=application.config["APP_BASE_URL"].startswith("https://"),
            content_security_policy={
                "default-src": "'self'",
                "script-src": [
                    "'self'",
                    "'unsafe-inline'",
                    "https://unpkg.com",
                    "https://cdn.jsdelivr.net",
                    "https://www.googletagmanager.com",
                ],
                "style-src": [
                    "'self'",
                    "'unsafe-inline'",
                    "https://unpkg.com",
                    "https://cdn.jsdelivr.net",
                    "https://fonts.googleapis.com",
                ],
                "img-src": ["'self'", "data:", "https:", "http:"],
                "font-src": ["'self'", "data:", "https://fonts.gstatic.com"],
                "connect-src": [
                    "'self'",
                    "https://www.google-analytics.com",
                    "https://region1.google-analytics.com",
                    "https://www.googletagmanager.com",
                ],
            },
            content_security_policy_nonce_in=None,
        )
        logger.info("Security features enabled")

    return application


if __name__ == "__main__":
    create_app().run(port=5003, host="127.0.0.1")
