# SPDX-License-Identifier: AGPL-3.0-or-later
import hmac
import logging
import os
import secrets
import threading
from urllib.parse import urljoin, urlparse

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
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
)
from flask_talisman import Talisman

import access_token  # noqa: F401
import app_config
import dashboard as dashboard_module  # noqa: F401
import dashboard_routes
import data_enricher
import database as db
import email_automation
import formation_wizard
import homepage_view_model
import ml_models
import registration
import security_utils
import tenant as tenant_module
from admin import admin_bp, require_admin
from api_public import public_api_bp
from cron import cron_bp
from email_utils import send_email
from health import health_bp
from leg_registry import registry_api_bp
from municipality import municipality_bp
from neighbor_view import collect_building_locations, find_provisional_matches
from rangliste import rangliste_bp
from registration import CONSENT_VERSION, parse_consents  # noqa: F401
from security_extensions import limiter
from security_utils import log_security_event
from self_host import self_host_bp
from utility_portal import utility_bp

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
    is_private_member_invoices = request.path.startswith("/dashboard/invoices")
    is_private_municipality_dashboard = request.path == "/gemeinde/dashboard" and bool(
        session.get("municipality_id")
    )
    if (
        request.path.startswith("/dashboard/access/")
        or request.path.startswith("/gemeinde/access/")
        or request.path.startswith("/registry/verify/")
        or is_private_dashboard
        or is_private_municipality_dashboard
        or is_private_leg_dashboard
        or is_private_leg_document
        or is_private_member_invoices
    ):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


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


# ===========================
# Routes
# ===========================


@main_bp.route("/")
def index():
    if session.get("dashboard_building_id"):
        return redirect("/dashboard")
    if session.get("municipality_id"):
        return redirect("/gemeinde/dashboard")
    territory = (
        g.tenant.get("territory", "zurich") if hasattr(g, "tenant") else "zurich"
    )
    model = homepage_view_model.build_homepage_view_model(
        territory, referral_code=request.args.get("ref", "")
    )
    return render_city_template(
        "index.html",
        user_count=model["stats"]["registered_buildings"],
        referral_code=model["referral"]["code"],
        referrer_street=model["referral"]["street"],
        ranking_best=model["ranking"]["best"],
        ranking_worst=model["ranking"]["needs_action"],
        ranking_total=model["ranking"]["total"],
    )


@main_bp.route("/login")
def login():
    return render_city_template("role_access.html")


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


@main_bp.route("/impressum")
def impressum():
    return render_city_template("impressum.html")


@main_bp.route("/datenschutz")
def datenschutz():
    return render_city_template("datenschutz.html")


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


@main_bp.route("/sitemap.xml")
def sitemap_xml():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    current_date = datetime.now(ZoneInfo("Europe/Zurich")).date().isoformat()
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
        ("/rangliste", "0.9", "daily", current_date),
        ("/rangliste/fortschritte", "0.8", "daily", current_date),
        ("/rangliste/vergleich", "0.7", "weekly", current_date),
        ("/rangliste/methodik", "0.6", "monthly", current_date),
        ("/api/v1/docs", "0.8", "weekly", current_date),
        ("/gemeinde/onboarding", "0.9", "weekly", current_date),
        ("/impressum", "0.3", "yearly", "2026-01-01"),
        ("/datenschutz", "0.3", "yearly", "2026-01-01"),
    ]
    xml = render_template(
        "sitemap.xml", site_url=current_app.config["SITE_URL"], pages=pages
    )
    return Response(xml, mimetype="application/xml")


@main_bp.route("/favicon.ico")
def favicon():
    return send_from_directory(
        current_app.static_folder,
        "favicon.ico",
        mimetype="image/vnd.microsoft.icon",
    )


## Health endpoints registered via health_bp


# --- Address API ---
@main_bp.route("/api/suggest_addresses")
@limiter.limit("30 per minute")
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


# --- Check Potential ---
@main_bp.route("/api/check_potential", methods=["POST"])
@limiter.limit("10 per minute")
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
@limiter.limit("5 per minute")
def api_register_anonymous():
    return _registration_response("anonymous")


@main_bp.route("/api/register_full", methods=["POST"])
@limiter.limit("5 per minute")
def api_register_full():
    return _registration_response("registered")


# --- Meter Data Upload ---
@main_bp.route("/api/meter-data/upload", methods=["POST"])
@limiter.limit("10 per minute")
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
@main_bp.route("/unsubscribe", methods=["GET", "POST"])
@limiter.limit("5 per minute")
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
@limiter.limit("10 per minute")
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


@main_bp.route("/registry/verify/<token>", methods=["GET", "POST"])
def verify_registry_entry(token):
    entry = db.get_registry_entry_by_verification_token(token)
    if not entry:
        abort(404)

    csrf_token = session.get("registry_verification_csrf_token")
    if not isinstance(csrf_token, str) or not csrf_token:
        csrf_token = secrets.token_urlsafe(32)
        session["registry_verification_csrf_token"] = csrf_token

    if request.method == "GET":
        return render_template(
            "registry_verify.html", entry=entry, csrf_token=csrf_token
        )

    submitted = request.form.get("csrf_token", "")
    if (
        not isinstance(submitted, str)
        or not submitted.isascii()
        or not hmac.compare_digest(submitted, csrf_token)
    ):
        abort(400)

    db.mark_registry_entry_verified(entry["id"])
    session.pop("registry_verification_csrf_token", None)
    return redirect("/?registry=verified")


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


@main_bp.route("/api/billing/community/<community_id>/period/<int:period_id>")
def api_billing_period(community_id, period_id):
    require_admin()
    try:
        period = db.get_billing_period(period_id, community_id)
    except db.BillingStoreError:
        abort(503)
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
    application.config.from_mapping(app_config.build_config(os.environ, config))

    public_site_base = app_config.validated_public_site_url(
        application.config["PUBLIC_SITE_URL"]
    )

    def public_site_url(path):
        if path.startswith("//"):
            raise ValueError("public site link must be a relative path")
        relative_path = path.lstrip("/")
        parsed_path = urlparse(relative_path)
        if parsed_path.scheme or parsed_path.netloc:
            raise ValueError("public site link must be a relative path")
        return urljoin(f"{public_site_base}/", relative_path)

    application.jinja_env.globals["public_site_url"] = public_site_url

    for blueprint in (
        main_bp,
        municipality_bp,
        registry_api_bp,
        public_api_bp,
        health_bp,
        utility_bp,
        rangliste_bp,
        self_host_bp,
        admin_bp,
        cron_bp,
    ):
        application.register_blueprint(blueprint)
    tenant_module.init_tenant_middleware(application, db=db)

    limiter.init_app(application)
    Talisman(
        application,
        force_https=application.config["APP_BASE_URL"].startswith("https://"),
        session_cookie_secure=application.config["SESSION_COOKIE_SECURE"],
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


def _dev_port(app_base_url, default=5003):
    return urlparse(app_base_url).port or default


if __name__ == "__main__":
    application = create_app()
    application.run(
        port=_dev_port(application.config["APP_BASE_URL"]), host="127.0.0.1"
    )
