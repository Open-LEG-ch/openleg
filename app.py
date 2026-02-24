import os
import time
import uuid
import math
import hashlib
import threading
import logging
import json
import csv
import io
from datetime import timedelta
from pathlib import Path
from flask import Flask, request, jsonify, render_template, abort, Response, g
from jinja2 import TemplateNotFound
import pandas as pd
import numpy as np
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

try:
    from scipy.spatial import ConvexHull
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

# --- Security imports ---
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    from flask_talisman import Talisman
    HAS_SECURITY_LIBS = True
except ImportError:
    HAS_SECURITY_LIBS = False

# --- Email imports ---
from email_utils import send_email, EMAIL_ENABLED, FROM_EMAIL

# --- Core modules ---
import data_enricher
import ml_models
import security_utils

# --- PostgreSQL Database ---
import database as db
USE_POSTGRES = db.is_db_available()
if not USE_POSTGRES:
    raise RuntimeError("PostgreSQL required. Set DATABASE_URL.")

# --- Multi-tenant ---
import tenant as tenant_module

# --- Email Automation ---
import email_automation

# --- Blueprints ---
from municipality import municipality_bp
from api_b2b import b2b_bp
from api_public import public_api_bp
from health import health_bp
from utility_portal import utility_bp

# --- Cron Secret ---
CRON_SECRET = os.getenv('CRON_SECRET', '').strip()

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# --- App ---
app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', os.urandom(32).hex())
app.config['SESSION_COOKIE_SECURE'] = os.getenv('SESSION_COOKIE_SECURE', 'False') == 'True'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = os.getenv('SESSION_COOKIE_SAMESITE', 'Lax')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(seconds=int(os.getenv('PERMANENT_SESSION_LIFETIME', 3600)))
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB for CSV uploads

# --- Basis-URL ---
APP_BASE_URL = os.getenv('APP_BASE_URL', 'http://localhost:5003')
SITE_URL = APP_BASE_URL.rstrip('/')
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

# --- Email ---
ADMIN_EMAIL = os.getenv('ADMIN_EMAIL', 'hallo@openleg.ch')
ADMIN_TOKEN = os.getenv('ADMIN_TOKEN', '').strip()

# --- Rate Limiting & Security ---
if HAS_SECURITY_LIBS:
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=["500 per hour"],
        storage_uri='memory://',
        strategy="fixed-window"
    )
    force_https = APP_BASE_URL.startswith('https://') if APP_BASE_URL else False
    Talisman(
        app,
        force_https=force_https,
        content_security_policy={
            'default-src': "'self'",
            'script-src': ["'self'", "'unsafe-inline'", "https://cdn.tailwindcss.com", "https://unpkg.com", "https://cdn.jsdelivr.net", "https://www.googletagmanager.com"],
            'style-src': ["'self'", "'unsafe-inline'", "https://unpkg.com", "https://cdn.jsdelivr.net"],
            'img-src': ["'self'", "data:", "https:", "http:"],
            'font-src': ["'self'", "data:"],
            'connect-src': ["'self'", "https://www.google-analytics.com", "https://www.googletagmanager.com"]
        },
        content_security_policy_nonce_in=None
    )
    logger.info("Security features enabled")
else:
    limiter = None

# --- Register Blueprints ---
app.register_blueprint(municipality_bp)
app.register_blueprint(b2b_bp)
app.register_blueprint(public_api_bp)
app.register_blueprint(health_bp)
app.register_blueprint(utility_bp)

# --- Multi-tenant middleware ---
tenant_module.init_tenant_middleware(app, db=db)


def render_city_template(template_name, **kwargs):
    """Render a per-city template with fallback to default."""
    tenant = getattr(g, 'tenant', tenant_module.DEFAULT_TENANT)
    kwargs.setdefault('tenant', tenant)
    kwargs.setdefault('site_url', SITE_URL)
    kwargs.setdefault('ga4_id', tenant.get('ga4_id') or os.getenv('GA4_MEASUREMENT_ID', ''))
    city_path = f"cities/{tenant['territory']}/{template_name}"
    try:
        return render_template(city_path, **kwargs)
    except TemplateNotFound:
        return render_template(template_name, **kwargs)


# --- Security Helpers ---
def log_security_event(event_type, details, level='INFO'):
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    log_message = f"[SECURITY] {event_type} | IP: {ip} | {details}"
    if level == 'WARNING':
        logger.warning(log_message)
    elif level == 'ERROR':
        logger.error(log_message)
    else:
        logger.info(log_message)


@app.after_request
def apply_basic_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


# --- Consent Helpers ---
CONSENT_VERSION = "2026-01-01"

def _coerce_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'ja', 'on')
    return False

def parse_consents(raw_consents):
    consents = raw_consents or {}
    return {
        'share_with_neighbors': _coerce_bool(consents.get('share_with_neighbors')),
        'share_with_utility': _coerce_bool(consents.get('share_with_utility')),
        'updates_opt_in': _coerce_bool(consents.get('updates_opt_in')),
        'consent_version': consents.get('consent_version') or CONSENT_VERSION,
        'consent_timestamp': time.time()
    }


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
        return getattr(g, 'tenant', {}).get('platform_name', 'OpenLEG')
    except RuntimeError:
        return 'OpenLEG'


def send_activity_notification(activity_type, details):
    name = _tenant_name()
    subject = f"{name}: {activity_type}"
    message_body = f"Neue Aktivität auf {name}:\n\nTyp: {activity_type}\n\nDetails:\n{details}"
    send_email(ADMIN_EMAIL, subject, message_body)


def send_confirmation_email(email, unsubscribe_url, building_id=None, address=None):
    name = _tenant_name()
    try:
        city = getattr(g, 'tenant', {}).get('city_name', 'Zürich')
    except RuntimeError:
        city = 'Zürich'
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
        if exclude_building_id and b.get('building_id') == exclude_building_id:
            continue
        lat = b.get('lat')
        lon = b.get('lon')
        if lat is None or lon is None:
            continue
        jlat, jlon = jitter_coordinates(float(lat), float(lon), seed=b.get('building_id'))
        locations.append({
            'lat': jlat,
            'lon': jlon,
            'type': b.get('user_type', 'anonymous')
        })
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
    if 'building_id' in buildings_with_clusters.columns:
        for _, row in buildings_with_clusters.iterrows():
            bid = row.get('building_id')
            cid = row.get('cluster', -1)
            if bid and cid >= 0:
                db.save_cluster(bid, cid)

    for community in ranked_communities:
        db.save_cluster_info(community['community_id'], community)

    logger.info(f"[ML] Clustering done: {len(ranked_communities)} clusters")


def find_provisional_matches(new_profile):
    """Fast provisional match search (distance only, no DBSCAN)."""
    profiles = db.get_all_building_profiles()
    if not profiles:
        return None

    new_coords = (new_profile['lat'], new_profile['lon'])
    provisional = [new_profile]

    for p in profiles:
        dist = ml_models.calculate_distance(new_coords[0], new_coords[1], float(p['lat']), float(p['lon']))
        if dist <= 150:
            provisional.append(p)

    if len(provisional) < 2:
        return None

    community_df = pd.DataFrame(provisional)
    autarky_score, _, _ = ml_models.calculate_community_autarky(community_df, None)

    members = [{'building_id': p.get('building_id', ''), 'lat': float(p['lat']), 'lon': float(p['lon'])} for p in provisional]
    return {
        'community_id': 'provisional',
        'num_members': len(members),
        'members': members,
        'autarky_percent': autarky_score * 100,
    }


def create_simple_polygon(coords):
    if len(coords) < 3:
        if len(coords) == 1:
            lat, lon = coords[0]
            o = 0.0005
            return [[lat-o, lon-o], [lat+o, lon-o], [lat+o, lon+o], [lat-o, lon+o], [lat-o, lon-o]]
        elif len(coords) == 2:
            lat1, lon1 = coords[0]
            lat2, lon2 = coords[1]
            o = 0.0003
            return [[lat1-o, lon1-o], [lat2+o, lon1-o], [lat2+o, lon2+o], [lat1-o, lon2+o], [lat1-o, lon1-o]]
    if HAS_SCIPY:
        try:
            points = np.array(coords)
            hull = ConvexHull(points)
            polygon = [coords[i] for i in hull.vertices]
            polygon.append(polygon[0])
            return polygon
        except:
            pass
    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    o = 0.0003
    return [[min(lats)-o, min(lons)-o], [max(lats)+o, min(lons)-o],
            [max(lats)+o, max(lons)+o], [min(lats)-o, max(lons)+o], [min(lats)-o, min(lons)-o]]


# ===========================
# Routes
# ===========================

@app.route("/")
def index():
    city_id = g.tenant.get('territory', 'zurich') if hasattr(g, 'tenant') else 'zurich'
    stats = db.get_stats(city_id=city_id)
    user_count = stats.get('total_buildings', 0)
    referral_code = request.args.get('ref', '')
    referrer_info = None
    if referral_code:
        referrer_info = db.get_building_by_referral_code(referral_code)
    return render_city_template('index.html',
        user_count=user_count,
        referral_code=referral_code,
        referrer_street=referrer_info.get('address', '').split(',')[0] if referrer_info else '',
    )


@app.route("/how-it-works")
def how_it_works():
    return render_city_template('how-it-works.html')


@app.route("/pricing")
def pricing():
    return render_city_template('pricing.html')


@app.route("/robots.txt")
def robots_txt():
    lines = [
        "User-agent: *", "Allow: /",
        "Disallow: /api/", "Disallow: /admin/", "Disallow: /confirm/", "Disallow: /unsubscribe/",
        f"Sitemap: {SITE_URL}/sitemap.xml"
    ]
    return Response("\n".join(lines) + "\n", mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    from datetime import datetime
    current_date = datetime.now().strftime("%Y-%m-%d")
    pages = [
        ("/", "1.0", "daily", current_date),
        ("/how-it-works", "0.8", "weekly", current_date),
        ("/pricing", "0.7", "monthly", current_date),
        ("/gemeinde/onboarding", "0.9", "weekly", current_date),
        ("/impressum", "0.3", "yearly", "2026-01-01"),
        ("/datenschutz", "0.3", "yearly", "2026-01-01"),
    ]
    xml = render_template("sitemap.xml", site_url=SITE_URL, pages=pages)
    return Response(xml, mimetype="application/xml")


## Health endpoints registered via health_bp


# --- Admin ---
def _require_admin():
    if not ADMIN_TOKEN:
        abort(404)
    token = request.headers.get('X-Admin-Token') or request.args.get('token') or ''
    if token != ADMIN_TOKEN:
        log_security_event("ADMIN_ACCESS_DENIED", "Invalid admin token", 'WARNING')
        abort(403)


@app.route("/admin/overview")
def admin_overview():
    _require_admin()
    stats = db.get_stats()
    email_stats = db.get_email_stats()
    consented = db.count_consented_buildings()
    municipalities = db.get_all_municipalities()
    return jsonify({
        "platform": "OpenLEG",
        "stats": stats,
        "email_stats": email_stats,
        "consented_buildings": consented,
        "municipalities": len(municipalities),
        "db_available": USE_POSTGRES
    })


@app.route("/admin/pipeline")
def admin_pipeline():
    _require_admin()
    status_filter = request.args.get("status")
    entries = db.get_vnb_pipeline(status_filter=status_filter)
    stats = db.get_vnb_pipeline_stats()
    return jsonify({"entries": entries, "stats": stats})


@app.route("/admin/export")
def admin_export():
    _require_admin()
    fmt = (request.args.get("format") or "json").lower()
    city_id = request.args.get("city_id")

    buildings = db.get_all_building_profiles(city_id=city_id)
    if fmt == "csv":
        output = io.StringIO()
        if buildings:
            writer = csv.DictWriter(output, fieldnames=buildings[0].keys())
            writer.writeheader()
            for row in buildings:
                writer.writerow(row)
        response = Response(output.getvalue(), mimetype="text/csv")
        response.headers["Content-Disposition"] = "attachment; filename=openleg_export.csv"
        return response
    return jsonify({"records": buildings, "count": len(buildings)})


# --- Address API ---
@app.route("/api/suggest_addresses")
@limiter.limit("30 per minute") if limiter else lambda f: f
def api_suggest_addresses():
    query = request.args.get('q', '').strip()
    query = security_utils.sanitize_string(query, max_length=100)
    if not query or len(query) < 2:
        return jsonify({"suggestions": []})
    limit = 15 if len(query) < 5 else 10
    plz_ranges = g.tenant.get('plz_ranges') if hasattr(g, 'tenant') else None
    suggestions_raw = data_enricher.get_address_suggestions(query, limit=limit, plz_ranges=plz_ranges)
    suggestions = []
    for s in suggestions_raw:
        if isinstance(s, dict) and s.get('label') and s.get('label').strip():
            label = security_utils.sanitize_string(s.get('label', ''), max_length=200)
            if label:
                suggestions.append({'label': label, 'lat': s.get('lat'), 'lon': s.get('lon'), 'plz': s.get('plz')})
    return jsonify({"suggestions": suggestions})


@app.route("/api/get_all_buildings")
def api_get_all_buildings():
    city_id = g.tenant.get('territory') if hasattr(g, 'tenant') else None
    locations = collect_building_locations(city_id=city_id)
    return jsonify({"buildings": locations})


@app.route("/api/get_all_clusters")
def api_get_all_clusters():
    clusters_raw = db.get_all_clusters()
    clusters = []
    for ci in clusters_raw:
        members = ci.get('members', [])
        if not members or len(members) < 2:
            continue
        coords = []
        member_list = []
        for mid in members:
            b = db.get_building(mid)
            if b and b.get('lat') and b.get('lon'):
                coords.append([float(b['lat']), float(b['lon'])])
                member_list.append({'building_id': mid, 'lat': float(b['lat']), 'lon': float(b['lon'])})
        if len(coords) >= 2:
            clusters.append({
                'cluster_id': ci.get('cluster_id'),
                'members': member_list,
                'polygon': create_simple_polygon(coords),
                'autarky_percent': float(ci.get('autarky_percent', 0)),
                'num_members': len(member_list)
            })
    return jsonify({"clusters": clusters})


# --- Check Potential ---
@app.route("/api/check_potential", methods=['POST'])
@limiter.limit("10 per minute") if limiter else lambda f: f
def api_check_potential():
    try:
        is_valid_size, size_error = security_utils.check_request_size(request)
        if not is_valid_size:
            return jsonify({"error": size_error}), 413
        if not request.json:
            return jsonify({"error": "Keine Daten empfangen."}), 400
        address = request.json.get('address', '').strip()
        is_valid, sanitized_address, error_msg = security_utils.validate_address(address)
        if not is_valid:
            return jsonify({"error": error_msg}), 400
        address = sanitized_address

        estimates, profiles = None, None
        try:
            estimates, profiles = data_enricher.get_energy_profile_for_address(address)
            if not estimates:
                estimates, profiles = data_enricher.get_mock_energy_profile_for_address(address)
        except Exception:
            estimates, profiles = data_enricher.get_mock_energy_profile_for_address(address)

        if not estimates:
            return jsonify({"error": "Adresse konnte nicht analysiert werden."}), 404
    except Exception as e:
        return jsonify({"error": f"Server-Fehler: {str(e)}"}), 500

    cluster_info = find_provisional_matches(estimates)
    if not cluster_info:
        return jsonify({"potential": False, "message": "Keine direkten Partner gefunden.", "profile_summary": estimates})
    return jsonify({"potential": True, "message": "Partner gefunden!", "cluster_info": cluster_info, "profile_summary": estimates})


# --- Registration ---
@app.route("/api/register_anonymous", methods=['POST'])
@limiter.limit("5 per minute") if limiter else lambda f: f
def api_register_anonymous():
    if not request.json:
        return jsonify({"error": "Keine Daten empfangen."}), 400
    is_valid_size, size_error = security_utils.check_request_size(request)
    if not is_valid_size:
        return jsonify({"error": size_error}), 413

    phone = (request.json.get('phone') or '').strip()
    email = (request.json.get('email') or '').strip()
    profile = request.json.get('profile')
    referral_code = (request.json.get('referral_code') or '').strip()

    referrer_id = None
    if referral_code:
        referrer = db.get_building_by_referral_code(referral_code)
        if referrer:
            referrer_id = referrer.get('building_id')

    is_valid_email, normalized_email, email_error = security_utils.validate_email_address(email)
    if not is_valid_email:
        return jsonify({"error": email_error}), 400
    email = normalized_email

    if phone:
        is_valid_phone, normalized_phone, phone_error = security_utils.validate_phone(phone)
        if not is_valid_phone:
            return jsonify({"error": phone_error}), 400
        phone = normalized_phone

    if not profile:
        return jsonify({"error": "Profildaten fehlen."}), 400
    building_id = profile.get('building_id')
    is_valid_id, id_error = security_utils.validate_building_id(building_id)
    if not is_valid_id:
        return jsonify({"error": id_error}), 400

    lat = profile.get('lat')
    lon = profile.get('lon')
    is_valid_coords, coords_error = security_utils.validate_coordinates(lat, lon)
    if not is_valid_coords:
        return jsonify({"error": coords_error}), 400

    consents = parse_consents(request.json.get('consents'))
    if not consents.get('share_with_neighbors') or not consents.get('share_with_utility'):
        return jsonify({"error": "Bitte stimmen Sie der Datenweitergabe zu."}), 400

    city_id = g.tenant.get('territory', 'zurich') if hasattr(g, 'tenant') else 'zurich'

    # Save to PostgreSQL
    db.save_building(
        building_id=building_id, email=email, profile=profile,
        consents=consents, user_type='anonymous', phone=phone,
        referrer_id=referrer_id, city_id=city_id
    )

    # Create unsubscribe token
    unsub_token = str(uuid.uuid4())
    db.save_token(unsub_token, building_id, 'unsubscribe')
    unsubscribe_url = f"{APP_BASE_URL}/unsubscribe/{unsub_token}"

    # Background tasks
    threading.Thread(target=send_confirmation_email, args=(email, unsubscribe_url, building_id, profile.get('address', '')), daemon=True).start()
    threading.Thread(target=run_full_ml_task, args=(building_id, city_id), daemon=True).start()
    threading.Thread(target=email_automation.schedule_sequence_for_user, args=(building_id, email), daemon=True).start()

    db.track_event('registration', building_id, {'type': 'anonymous', 'city_id': city_id})

    # Build response
    cluster_info = find_provisional_matches(profile)
    locations = collect_building_locations(city_id=city_id, exclude_building_id=building_id)
    referral_link = None
    ref_code = db.get_referral_code(building_id)
    if ref_code:
        referral_link = f"{APP_BASE_URL}/?ref={ref_code}"

    payload = {
        "buildings": locations,
        "match_found": bool(cluster_info),
        "verification_email_sent": True,
        "referral_link": referral_link
    }
    if cluster_info:
        payload["cluster_info"] = cluster_info
    return jsonify(payload)


@app.route("/api/register_full", methods=['POST'])
@limiter.limit("5 per minute") if limiter else lambda f: f
def api_register_full():
    if not request.json:
        return jsonify({"error": "Keine Daten empfangen."}), 400
    is_valid_size, size_error = security_utils.check_request_size(request)
    if not is_valid_size:
        return jsonify({"error": size_error}), 413

    profile = request.json.get('profile')
    email = (request.json.get('email') or '').strip()
    phone = (request.json.get('phone') or '').strip()
    referral_code = (request.json.get('referral_code') or '').strip()

    referrer_id = None
    if referral_code:
        referrer = db.get_building_by_referral_code(referral_code)
        if referrer:
            referrer_id = referrer.get('building_id')

    is_valid_email, normalized_email, email_error = security_utils.validate_email_address(email)
    if not is_valid_email:
        return jsonify({"error": email_error}), 400
    email = normalized_email

    if phone:
        is_valid_phone, normalized_phone, phone_error = security_utils.validate_phone(phone)
        if not is_valid_phone:
            return jsonify({"error": phone_error}), 400
        phone = normalized_phone

    if not profile:
        return jsonify({"error": "Profildaten fehlen."}), 400
    building_id = profile.get('building_id')
    is_valid_id, id_error = security_utils.validate_building_id(building_id)
    if not is_valid_id:
        return jsonify({"error": id_error}), 400

    lat = profile.get('lat')
    lon = profile.get('lon')
    is_valid_coords, coords_error = security_utils.validate_coordinates(lat, lon)
    if not is_valid_coords:
        return jsonify({"error": coords_error}), 400

    consents = parse_consents(request.json.get('consents'))
    if not consents.get('share_with_neighbors') or not consents.get('share_with_utility'):
        return jsonify({"error": "Bitte stimmen Sie der Datenweitergabe zu."}), 400

    city_id = g.tenant.get('territory', 'zurich') if hasattr(g, 'tenant') else 'zurich'

    db.save_building(
        building_id=building_id, email=email, profile=profile,
        consents=consents, user_type='registered', phone=phone,
        referrer_id=referrer_id, city_id=city_id
    )

    unsub_token = str(uuid.uuid4())
    db.save_token(unsub_token, building_id, 'unsubscribe')
    unsubscribe_url = f"{APP_BASE_URL}/unsubscribe/{unsub_token}"

    threading.Thread(target=send_confirmation_email, args=(email, unsubscribe_url, building_id, profile.get('address', '')), daemon=True).start()
    threading.Thread(target=run_full_ml_task, args=(building_id, city_id), daemon=True).start()
    threading.Thread(target=email_automation.schedule_sequence_for_user, args=(building_id, email), daemon=True).start()

    db.track_event('registration', building_id, {'type': 'registered', 'city_id': city_id})

    cluster_info = find_provisional_matches(profile)
    locations = collect_building_locations(city_id=city_id, exclude_building_id=building_id)
    referral_link = None
    ref_code = db.get_referral_code(building_id)
    if ref_code:
        referral_link = f"{APP_BASE_URL}/?ref={ref_code}"

    payload = {
        "buildings": locations,
        "match_found": bool(cluster_info),
        "verification_email_sent": True,
        "referral_link": referral_link
    }
    if cluster_info:
        payload["cluster_info"] = cluster_info
    return jsonify(payload)


# --- Meter Data Upload ---
@app.route("/api/meter-data/upload", methods=['POST'])
@limiter.limit("10 per minute") if limiter else lambda f: f
def api_meter_data_upload():
    import meter_data
    data = request.json or {}
    building_id = data.get('building_id', '').strip()
    csv_content = data.get('csv_content', '')
    tier = int(data.get('tier', 1))

    if not building_id or not csv_content:
        return jsonify({"error": "building_id und csv_content erforderlich."}), 400

    # Verify building exists
    building = db.get_building(building_id)
    if not building:
        return jsonify({"error": "Gebäude nicht gefunden."}), 404

    # Save consent tier
    db.save_data_consent(building_id, tier=tier,
        share_municipality=True,
        share_research=(tier >= 2),
        share_providers=(tier >= 3))

    result = meter_data.ingest_csv(building_id, csv_content, source='csv_upload')
    return jsonify(result)


@app.route("/meter-upload")
def meter_upload_page():
    return render_city_template('meter_upload.html')


# --- Unsubscribe ---
@app.route("/impressum")
def impressum():
    return render_city_template("impressum.html")

@app.route("/datenschutz")
def datenschutz():
    return render_city_template("datenschutz.html")


@app.route("/unsubscribe", methods=["GET", "POST"])
@limiter.limit("5 per minute") if limiter else lambda f: f
def unsubscribe_page():
    status = None
    message = None
    email_value = ""

    if request.method == "POST":
        email_value = (request.form.get("email") or "").strip()
        is_valid_email, normalized_email, email_error = security_utils.validate_email_address(email_value)
        if not is_valid_email:
            status = "error"
            message = email_error
        else:
            email_value = normalized_email
            matches = db.get_building_by_email(email_value)
            if not matches:
                status = "info"
                message = "Für diese E-Mail-Adresse wurde kein Eintrag gefunden."
            else:
                for m in matches:
                    db.delete_building(m['building_id'])
                status = "success"
                message = "Ihre Daten wurden erfolgreich gelöscht."

    return render_city_template("unsubscribe.html", status=status, message=message, email=email_value)


@app.route("/unsubscribe/<token>")
@limiter.limit("10 per minute") if limiter else lambda f: f
def unsubscribe_token(token):
    is_valid_token, token_error = security_utils.validate_token(token)
    if not is_valid_token:
        return "<h1>Ungültiger Link</h1>", 400

    token_info = db.get_token(token)
    if not token_info:
        return "<h1>Link ungültig oder bereits verwendet</h1>", 404

    building_id = token_info['building_id']
    db.use_token(token)
    db.delete_building(building_id)
    db.cancel_emails_for_building(building_id)
    return "<h1>Abmeldung erfolgreich</h1><p>Ihre Daten wurden gelöscht.</p>"


# --- Dashboard ---
@app.route("/dashboard")
def dashboard():
    building_id = request.args.get('bid', '').strip()
    if not building_id:
        return render_city_template('dashboard.html', error="Kein Profil angegeben.", user=None)

    user = db.get_building_for_dashboard(building_id)
    if not user:
        return render_city_template('dashboard.html', error="Profil nicht gefunden.", user=None)

    score = 0
    checks = []
    if user.get('verified'):
        score += 25
        checks.append(('E-Mail bestätigt', True))
    else:
        checks.append(('E-Mail bestätigt', False))
    if user.get('annual_consumption_kwh'):
        score += 25
        checks.append(('Verbrauchsdaten hinterlegt', True))
    else:
        checks.append(('Verbrauchsdaten hinterlegt', False))
    if user.get('share_with_utility'):
        score += 25
        checks.append(('EVU-Einwilligung erteilt', True))
    else:
        checks.append(('EVU-Einwilligung erteilt', False))
    if user.get('share_with_neighbors'):
        score += 25
        checks.append(('Nachbar-Einwilligung erteilt', True))
    else:
        checks.append(('Nachbar-Einwilligung erteilt', False))

    neighbor_count = 0
    referral_link = ''
    lat = user.get('lat')
    lon = user.get('lon')
    city_id = g.tenant.get('territory') if hasattr(g, 'tenant') else None
    if lat and lon:
        neighbor_count = db.get_neighbor_count_near(float(lat), float(lon), city_id=city_id)
    ref_code = db.get_referral_code(building_id)
    if ref_code:
        referral_link = f"{APP_BASE_URL}/?ref={ref_code}"

    return render_city_template('dashboard.html',
        user=user, readiness_score=score, checks=checks,
        neighbor_count=neighbor_count, referral_link=referral_link, error=None)


# --- Referral System ---
@app.route("/api/referral/stats/<building_id>")
def api_referral_stats(building_id):
    stats = db.get_referral_stats(building_id)
    referral_code = db.get_referral_code(building_id)
    return jsonify({
        "referral_code": referral_code,
        "referral_link": f"{APP_BASE_URL}/?ref={referral_code}" if referral_code else None,
        "total_referrals": stats.get('total_referrals', 0)
    })


@app.route("/api/referral/leaderboard")
def api_referral_leaderboard():
    city_id = g.tenant.get('territory') if hasattr(g, 'tenant') else None
    leaderboard = db.get_referral_leaderboard(limit=10, city_id=city_id)
    for entry in leaderboard:
        street = entry.get('street', '')
        entry['display_name'] = street[:15] + '...' if len(street) > 15 else street
    return jsonify({"leaderboard": leaderboard})


@app.route("/api/stats/public")
def api_public_stats():
    city_id = g.tenant.get('territory') if hasattr(g, 'tenant') else None
    stats = db.get_stats(city_id=city_id)
    return jsonify({
        "total_users": stats.get('total_buildings', 0),
        "registrations_today": stats.get('registrations_today', 0)
    })


@app.route("/api/stats/live")
def api_live_stats():
    city_id = g.tenant.get('territory', 'zurich') if hasattr(g, 'tenant') else None
    stats = db.get_stats(city_id=city_id)
    return jsonify({
        "total_registered": stats.get('total_buildings', 0),
        "last_24h": stats.get('registrations_today', 0),
        "clusters_ready": 0,
        "avg_savings_chf": 520
    })


# --- Savings Calculator ---
@app.route("/api/calculate_savings", methods=['POST'])
def api_calculate_savings():
    data = request.json or {}
    consumption = float(data.get('consumption_kwh', 4500))
    has_solar = bool(data.get('has_solar', False))
    pv_kwp = float(data.get('pv_kwp', 0))

    base_rate_saving = 0.04
    annual_base = consumption * base_rate_saving
    solar_savings = 0
    if has_solar and pv_kwp > 0:
        solar_yield = g.tenant.get('solar_kwh_per_kwp', 1000) if hasattr(g, 'tenant') else 1000
        annual_production = pv_kwp * solar_yield
        export_to_leg = annual_production * 0.65
        solar_savings = export_to_leg * 0.08

    total_annual = min(annual_base + solar_savings, 1200)
    return jsonify({
        "annual_savings_chf": round(total_annual, 2),
        "monthly_savings_chf": round(total_annual / 12, 2),
        "five_year_total_chf": round(total_annual * 5, 2),
        "has_solar_bonus": has_solar and pv_kwp > 0,
        "consumption_kwh": consumption,
    })


# --- Formation API ---
@app.route("/api/formation/optimize", methods=['POST'])
def api_formation_optimize():
    """LEG optimization endpoint."""
    import formation_wizard
    data = request.json or {}
    building_id = data.get('building_id', '').strip()
    if not building_id:
        return jsonify({"error": "building_id required"}), 400

    clusters = formation_wizard.get_formable_clusters(db, building_id)
    return jsonify({"clusters": clusters})


@app.route("/api/formation/financial-model", methods=['POST'])
def api_formation_financial_model():
    """Savings projection for a LEG."""
    import formation_wizard
    data = request.json or {}
    consumption = float(data.get('consumption_kwh', 4500))
    pv_kwp = float(data.get('pv_kwp', 0))
    community_size = int(data.get('community_size', 5))
    solar_kwh = g.tenant.get('solar_kwh_per_kwp', 1000) if hasattr(g, 'tenant') else 1000

    result = formation_wizard.calculate_savings_estimate(consumption, pv_kwp, community_size, solar_kwh)
    return jsonify(result)


# --- Cron ---
@app.route("/api/cron/process-emails", methods=['POST'])
def api_cron_process_emails():
    secret = request.headers.get('X-Cron-Secret') or request.args.get('secret') or ''
    if CRON_SECRET and secret != CRON_SECRET:
        abort(403)
    result = email_automation.process_email_queue(app=app)
    return jsonify(result)


@app.route("/api/cron/refresh-public-data", methods=['POST'])
def api_cron_refresh_public_data():
    secret = request.headers.get('X-Cron-Secret') or request.args.get('secret') or ''
    if CRON_SECRET and secret != CRON_SECRET:
        abort(403)
    import public_data
    result = public_data.refresh_canton('ZH')
    return jsonify(result)


@app.route("/api/cron/refresh-insights", methods=['POST'])
def api_cron_refresh_insights():
    secret = request.headers.get('X-Cron-Secret') or request.args.get('secret') or ''
    if CRON_SECRET and secret != CRON_SECRET:
        abort(403)
    import insights_engine
    result = insights_engine.refresh_all_insights()
    return jsonify(result)


@app.route("/api/email/stats")
def api_email_stats():
    _require_admin()
    return jsonify(db.get_email_stats())


if __name__ == "__main__":
    app.run(debug=True, port=5003, host='127.0.0.1')
