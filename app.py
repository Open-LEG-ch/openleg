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
import agentmail_client

# --- Core modules ---
import data_enricher
import ml_models
import security_utils
import cache as redis_cache

# --- PostgreSQL Database ---
import database as db
USE_POSTGRES = db.is_db_available()
if not USE_POSTGRES:
    raise RuntimeError("PostgreSQL required. Set DATABASE_URL.")

# --- Multi-tenant ---
import tenant as tenant_module

# --- Email Automation ---
import email_automation

# --- Municipality Seeder ---
import municipality_seeder

# --- Event Hooks ---
import event_hooks

# --- Blueprints ---
from municipality import municipality_bp
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
SIMULATION_CACHE_TTL_SECONDS = int(os.getenv("SIMULATION_CACHE_TTL_SECONDS", "86400"))
SIMULATION_CACHE_VERSION = os.getenv("SIMULATION_CACHE_VERSION", "v2")
PROVISIONAL_SIM_NUM_INTERVALS = int(os.getenv("PROVISIONAL_SIM_NUM_INTERVALS", "672"))
PROVISIONAL_SIM_START = os.getenv("PROVISIONAL_SIM_START", "2025-01-01 00:00:00")

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
INTERNAL_TOKEN = os.getenv('INTERNAL_TOKEN', '').strip()
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '').strip()
TELEGRAM_WEBHOOK_SECRET = os.getenv('TELEGRAM_WEBHOOK_SECRET', '').strip()


def _relay_to_telegram(job_name, summary, status):
    """Fire-and-forget relay of LEA reports to Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    icon = '✅' if status == 'ok' else '⚠️'
    text = f"{icon} *LEA: {job_name}*\n{summary[:3000]}"
    def _send():
        try:
            import urllib.request
            payload = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True}).encode()
            req = urllib.request.Request(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            logger.warning(f"[Telegram] relay failed: {e}")
    threading.Thread(target=_send, daemon=True).start()


def _send_telegram_message(text, reply_to_message_id=None):
    """Sync Telegram send, returns message_id or None."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return None
    try:
        import urllib.request
        payload = json.dumps({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
            **({"reply_to_message_id": reply_to_message_id} if reply_to_message_id else {})
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"}
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        return data.get("result", {}).get("message_id") if data.get("ok") else None
    except Exception as e:
        logger.warning(f"[Telegram] send failed: {e}")
        return None


def _execute_approved_action(decision):
    """Execute action after CEO approval. Returns (success, detail)."""
    activity = decision.get('activity', '')
    payload = decision.get('payload') or {}
    if isinstance(payload, str):
        payload = json.loads(payload)

    if activity == 'outreach':
        to = payload.get('to', '')
        subject = payload.get('subject', '')
        text = payload.get('text', '')
        inbox = payload.get('inbox', 'lea')
        if not to or not subject or not text:
            return False, "Missing to/subject/text in payload"
        if inbox == 'lea' and AGENTMAIL_LEA_INBOX:
            msg_id = agentmail_client.send_message(
                inbox_id=AGENTMAIL_LEA_INBOX,
                to=to, subject=subject, text=text
            )
            sent = msg_id is not None
        else:
            sent = send_email(to, subject, text)
        if sent:
            db.track_event('approved_email_sent', data={
                'to': to, 'subject': subject, 'request_id': decision.get('request_id')
            })
        return sent, f"Email to {to}: {'sent' if sent else 'failed'}"

    if activity == 'trigger_email':
        building_id = payload.get('building_id', '')
        template_key = payload.get('template_key', '')
        send_at = payload.get('send_at', '')
        if not building_id or not template_key:
            return False, "Missing building_id/template_key in payload"
        building = db.get_building(building_id)
        if not building:
            return False, f"Building {building_id} not found"
        email = building.get('email', '')
        try:
            with db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO scheduled_emails (building_id, email, template_key, send_at, status, created_at)
                           VALUES (%s, %s, %s, %s, 'pending', NOW()) RETURNING id""",
                        (building_id, email, template_key, send_at or 'NOW()')
                    )
                    row = cur.fetchone()
            db.track_event('lea_trigger_email', data={
                'building_id': building_id, 'template_key': template_key,
                'request_id': decision.get('request_id')
            })
            return True, f"Scheduled email #{row['id']} for {email}"
        except Exception as e:
            return False, f"trigger_email failed: {e}"

    if activity == 'update_consent':
        building_id = payload.get('building_id', '')
        fields = {k: v for k, v in payload.items() if k in ('share_with_neighbors', 'share_with_utility', 'updates_opt_in') and v is not None}
        if not building_id or not fields:
            return False, "Missing building_id or consent fields"
        try:
            sets = [f"{k} = %s" for k in fields]
            vals = list(fields.values()) + [building_id]
            with db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(f"UPDATE consents SET {', '.join(sets)} WHERE building_id = %s", vals)
                    if cur.rowcount == 0:
                        return False, f"No consent row for {building_id}"
            db.track_event('lea_update_consent', data={
                'building_id': building_id, 'fields': fields,
                'request_id': decision.get('request_id')
            })
            return True, f"Updated consent for {building_id}: {fields}"
        except Exception as e:
            return False, f"update_consent failed: {e}"

    if activity == 'generate_leg_document':
        community_id = payload.get('community_id', '')
        doc_type = payload.get('doc_type', '')
        if not community_id or not doc_type:
            return False, "Missing community_id/doc_type"
        try:
            import urllib.request
            body = json.dumps({"community_id": community_id, "doc_type": doc_type}).encode()
            req = urllib.request.Request(
                f"http://localhost:5000/api/formation/generate-document",
                data=body, headers={"Content-Type": "application/json"}
            )
            resp = urllib.request.urlopen(req, timeout=30)
            result = json.loads(resp.read())
            db.track_event('lea_generate_leg_document', data={
                'community_id': community_id, 'doc_type': doc_type,
                'request_id': decision.get('request_id')
            })
            return True, f"Generated {doc_type} for {community_id}"
        except Exception as e:
            return False, f"generate_leg_document failed: {e}"

    return False, f"Unknown activity: {activity}"


# --- Rate Limiting & Security ---
if HAS_SECURITY_LIBS:
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=["500 per hour"],
        storage_uri=os.getenv('REDIS_URL', 'redis://redis:6379/1'),
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


def _require_dashboard_token():
    """Validate dashboard token from query param or header. Returns building_id or aborts."""
    token = request.args.get('token', '') or request.headers.get('X-Dashboard-Token', '')
    token = token.strip()
    if not token:
        abort(403)
    token_info = db.get_token(token)
    if not token_info or token_info.get('token_type') != 'dashboard':
        abort(403)
    return token_info['building_id']


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


def _sim_redis_key(cache_key):
    return f"sim:{cache_key}"


def _build_sim_cache_backend(city_id=None, sim_version=SIMULATION_CACHE_VERSION):
    def _get(cache_key):
        redis_value = redis_cache.cache_get(_sim_redis_key(cache_key))
        if isinstance(redis_value, dict):
            return redis_value
        row = db.get_simulation_cache(cache_key)
        if isinstance(row, dict):
            payload = row.get("result_json", row)
            if isinstance(payload, dict):
                redis_cache.cache_set(_sim_redis_key(cache_key), payload, ttl=3600)
                return payload
        return None

    def _set(cache_key, result, ttl_seconds):
        ttl = max(int(ttl_seconds or SIMULATION_CACHE_TTL_SECONDS), 1)
        db_ok = db.set_simulation_cache(
            cache_key=cache_key,
            result=result,
            ttl_seconds=ttl,
            city_id=city_id,
            building_ids_hash=cache_key,
            sim_version=sim_version,
        )
        redis_cache.cache_set(_sim_redis_key(cache_key), result, ttl=min(ttl, 3600))
        return db_ok

    return {"get": _get, "set": _set}


def run_full_ml_task(new_building_id=None, city_id=None):
    """Background ML clustering task using PostgreSQL data."""
    logger.info("[ML] Starting background clustering...")
    db.purge_simulation_cache(city_id=city_id)
    profiles = db.get_all_building_profiles(city_id=city_id)
    if len(profiles) < 2:
        logger.info("[ML] Not enough buildings for clustering.")
        return

    cache_backend = _build_sim_cache_backend(city_id=city_id, sim_version=SIMULATION_CACHE_VERSION)
    building_data = pd.DataFrame(profiles)
    ranked_communities, buildings_with_clusters = ml_models.find_optimal_communities(
        building_data,
        radius_meters=150,
        min_community_size=2,
        city_id=city_id,
        sim_version=SIMULATION_CACHE_VERSION,
        cache_backend=cache_backend,
        cache_ttl_seconds=SIMULATION_CACHE_TTL_SECONDS,
        strategy="hybrid",
    )

    db.clear_clusters_for_city(city_id=city_id)

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


def find_provisional_matches(new_profile, city_id=None):
    """Fast provisional match search (distance only, no DBSCAN)."""
    profiles = db.get_all_building_profiles(city_id=city_id)
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
    cache_backend = _build_sim_cache_backend(city_id=city_id, sim_version=SIMULATION_CACHE_VERSION)
    window = {
        "start": PROVISIONAL_SIM_START,
        "num_intervals": PROVISIONAL_SIM_NUM_INTERVALS,
    }
    cache_key = ml_models.build_community_signature(
        community_df,
        city_id=city_id,
        sim_version=f"{SIMULATION_CACHE_VERSION}-provisional",
    )
    details = ml_models.calculate_community_autarky_details(
        community_buildings_df=community_df,
        all_profiles=None,
        profile_provider=None,
        cache_backend=cache_backend,
        cache_key=cache_key,
        cache_ttl_seconds=min(SIMULATION_CACHE_TTL_SECONDS, 3600),
        strategy="hybrid",
        window=window,
    )

    members = [{'building_id': p.get('building_id', ''), 'lat': float(p['lat']), 'lon': float(p['lon'])} for p in provisional]
    return {
        'community_id': 'provisional',
        'num_members': len(members),
        'members': members,
        'autarky_percent': details.get('autarky_score', 0.0) * 100,
        'confidence_percent': details.get('confidence_percent', 0.0),
        'profile_data_mix': details.get('profile_data_mix', 'mock'),
        'cache_hit': details.get('cache_hit', False),
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
# Event Hooks
# ===========================

def _on_formation_threshold_reached(payload):
    """Send congratulation email when community reaches minimum members."""
    community_id = payload.get('community_id', '')
    if not community_id:
        return
    db.track_event('formation_threshold_reached', data={'community_id': community_id})
    try:
        members = db.get_community_members(community_id)
        community = db.get_community(community_id)
        community_name = community.get('name', 'Ihre Stromgemeinschaft') if community else 'Ihre Stromgemeinschaft'
        member_count = len(members) if members else 0
        for m in (members or []):
            email = m.get('email', '')
            if not email:
                continue
            html = render_template('emails/formation_ready.html',
                                   community_name=community_name,
                                   member_count=member_count,
                                   formation_url=f"{SITE_URL}/gemeinde/formation?community={community_id}")
            send_email(email, f"Ihre Stromgemeinschaft kann starten: {community_name}", html)
    except Exception as e:
        logger.error(f"[formation_threshold] email failed: {e}")

event_hooks.register('formation_threshold_reached', _on_formation_threshold_reached)


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

@app.route("/fuer-gemeinden")
def fuer_gemeinden():
    return render_city_template('fuer_gemeinden.html')

@app.route("/fuer-bewohner")
def fuer_bewohner():
    city_id = g.tenant.get('territory', 'zurich') if hasattr(g, 'tenant') and g.tenant else 'zurich'
    stats = db.get_stats(city_id=city_id)
    user_count = stats.get('total_buildings', 0)
    return render_city_template('fuer_bewohner.html', user_count=user_count)


@app.route("/pricing")
def pricing():
    return render_city_template('pricing.html')


@app.route("/transparenz")
def vnb_transparenz():
    from public_data import compute_vnb_transparency_score
    kanton_filter = request.args.get('kanton', '').strip().upper()
    year = int(request.args.get('year', 2026))

    tariffs_by_op = db.get_all_elcom_tariffs_by_operator(year)
    profiles = db.get_all_municipality_profiles()
    last_refresh_info = db.get_elcom_last_refresh()
    # Map BFS -> municipality info
    bfs_map = {p['bfs_number']: p for p in profiles}

    rankings = []
    all_municipalities_covered = set()
    score_sum = 0.0
    for operator, tariffs in tariffs_by_op.items():
        # Find municipalities served by this operator
        bfs_set = {t.get('bfs_number') for t in tariffs if t.get('bfs_number')}
        munis = [bfs_map.get(b, {}) for b in bfs_set]

        # Apply kanton filter
        if kanton_filter:
            munis_filtered = [m for m in munis if m.get('kanton', '').upper() == kanton_filter]
            if not munis_filtered and munis:
                continue
            munis = munis_filtered

        muni_info = sorted(
            [{'name': m.get('name', ''), 'bfs_number': m.get('bfs_number')} for m in munis if m.get('name')],
            key=lambda x: x['name']
        )
        muni_names = [m['name'] for m in muni_info]
        score = compute_vnb_transparency_score(tariffs, municipalities_served=len(bfs_set))
        score_sum += score
        all_municipalities_covered |= bfs_set
        rankings.append({
            'operator_name': operator,
            'transparency_score': score,
            'municipalities_served': len(bfs_set),
            'municipality_names': muni_names[:10],
            'municipality_info': muni_info[:10],
            'bfs_numbers': list(bfs_set),
        })

    rankings.sort(key=lambda x: x['transparency_score'], reverse=True)

    # Stats
    operator_count = len(rankings)
    avg_score = round(score_sum / operator_count, 1) if operator_count else 0
    municipalities_covered = len(all_municipalities_covered)

    # Unique kantons for filter dropdown
    kantons = sorted({p.get('kanton', '') for p in profiles if p.get('kanton')})

    return render_city_template('vnb_transparenz.html',
                                rankings=rankings, kantons=kantons,
                                kanton_filter=kanton_filter, year=year,
                                last_refresh=last_refresh_info,
                                operator_count=operator_count,
                                avg_score=avg_score,
                                municipalities_covered=municipalities_covered)


@app.route("/gemeinde/toolkit")
def gemeinde_toolkit():
    return render_city_template('gemeinde/toolkit.html')


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
        ("/fuer-gemeinden", "0.8", "weekly", current_date),
        ("/fuer-bewohner", "0.8", "weekly", current_date),
        ("/pricing", "0.7", "monthly", current_date),
        ("/transparenz", "0.7", "weekly", current_date),
        ("/gemeinde/toolkit", "0.6", "monthly", current_date),
        ("/gemeinde/onboarding", "0.9", "weekly", current_date),
        ("/impressum", "0.3", "yearly", "2026-01-01"),
        ("/datenschutz", "0.3", "yearly", "2026-01-01"),
    ]
    # Add all municipality profile pages
    try:
        profiles = db.get_all_municipality_profiles()
        for p in profiles:
            bfs = p.get('bfs_number')
            if bfs:
                pages.append((f"/gemeinde/profil/{bfs}", "0.6", "weekly", current_date))
    except Exception:
        pass
    xml = render_template("sitemap.xml", site_url=SITE_URL, pages=pages)
    return Response(xml, mimetype="application/xml")


## Health endpoints registered via health_bp


# --- Cron Helper ---
def _require_cron_secret():
    """Fail-closed: abort(503) when CRON_SECRET unconfigured, header-only check."""
    if not CRON_SECRET:
        abort(503)
    secret = request.headers.get('X-Cron-Secret', '')
    if secret != CRON_SECRET:
        abort(403)


# --- Admin ---
def _require_admin():
    if not ADMIN_TOKEN:
        abort(404)
    token = request.headers.get('X-Admin-Token', '')
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

    if 'text/html' in (request.headers.get('Accept') or ''):
        return render_template('admin/pipeline.html', entries=entries, stats=stats)
    return jsonify({"entries": entries, "stats": stats})


@app.route("/admin/strategy")
def admin_strategy():
    _require_admin()
    from email_automation import monitor_formation_pipeline
    stats = db.get_stats()
    email_stats = db.get_email_stats()
    pipeline = monitor_formation_pipeline()
    if 'text/html' in (request.headers.get('Accept') or ''):
        return render_template('admin/strategy.html',
                               stats=stats, email_stats=email_stats, pipeline=pipeline)
    return jsonify({"stats": stats, "email_stats": email_stats, "pipeline": pipeline})


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


# --- LEA Reports ---
@app.route("/api/internal/lea-report", methods=['POST'])
def api_internal_lea_report():
    token = request.headers.get('X-Internal-Token', '')
    if not INTERNAL_TOKEN or token != INTERNAL_TOKEN:
        abort(403)
    data = request.get_json(silent=True) or {}
    job_name = data.get('job_name', 'unknown')
    summary = data.get('summary', '')
    status = data.get('status', 'ok')
    db.save_lea_report(job_name, summary, status)
    _relay_to_telegram(job_name, summary, status)
    return jsonify({"ok": True})


@app.route("/admin/lea-reports")
def admin_lea_reports():
    _require_admin()
    reports = db.get_lea_reports(limit=50)
    return jsonify({"reports": reports})


# --- Agent Email Sending ---
AGENTMAIL_LEA_INBOX = os.getenv('AGENTMAIL_LEA_INBOX', '').strip()
AGENTMAIL_WEBHOOK_SECRET = os.getenv('AGENTMAIL_WEBHOOK_SECRET', '').strip()
AGENT_EMAIL_ENABLED = os.getenv('AGENT_EMAIL_ENABLED', 'false').lower() == 'true'


@app.route("/api/internal/send-email", methods=['POST'])
def api_internal_send_email():
    """Send email via AgentMail/SMTP. Used by LEA agent via MCP."""
    token = request.headers.get('X-Internal-Token', '')
    if not INTERNAL_TOKEN or token != INTERNAL_TOKEN:
        abort(403)
    if not AGENT_EMAIL_ENABLED:
        return jsonify({"error": "Agent email sending disabled (AGENT_EMAIL_ENABLED=false)"}), 403
    data = request.get_json(silent=True) or {}
    to = data.get('to', '').strip()
    subject = data.get('subject', '').strip()
    text = data.get('text', '').strip()
    html_body = data.get('html', '').strip() or None
    inbox = data.get('inbox', 'transactional')
    if not to or not subject or not text:
        return jsonify({"error": "to, subject, text required"}), 400
    # Choose inbox
    if inbox == 'lea' and AGENTMAIL_LEA_INBOX:
        msg_id = agentmail_client.send_message(
            inbox_id=AGENTMAIL_LEA_INBOX,
            to=to, subject=subject, text=text, html=html_body
        )
        sent = msg_id is not None
    else:
        sent = send_email(to, subject, text, html=bool(html_body), from_email=None)
    # Log + notify
    db.track_event('email_sent', data={
        'to': to, 'subject': subject, 'inbox': inbox, 'sent': sent
    })
    _relay_to_telegram('email_sent', f"To: {to}\nSubject: {subject}\nSent: {sent}", 'ok' if sent else 'error')
    return jsonify({"ok": sent, "inbox": inbox})


@app.route("/webhook/agentmail", methods=['POST'])
def webhook_agentmail():
    """Handle inbound AgentMail webhooks (message received, etc)."""
    if AGENTMAIL_WEBHOOK_SECRET:
        sig = request.headers.get('X-Webhook-Secret', '')
        if sig != AGENTMAIL_WEBHOOK_SECRET:
            abort(403)
    data = request.get_json(silent=True) or {}
    event_type = data.get('type', data.get('event', 'unknown'))
    payload = data.get('data', data)
    logger.info(f"[AgentMail Webhook] {event_type}: {json.dumps(payload)[:500]}")
    db.track_event('agentmail_webhook', data={'event': event_type, 'payload': payload})
    # Classify and save inbound message
    if 'received' in event_type:
        sender = payload.get('from', payload.get('sender', 'unknown'))
        subj = payload.get('subject', '(no subject)')
        body = payload.get('body', payload.get('text', ''))[:2000]
        msg_id = payload.get('message_id', payload.get('id', ''))
        # Keyword classification
        combined = (subj + ' ' + body).lower()
        if any(w in combined for w in ['spam', 'unsubscribe', 'newsletter', 'werbung']):
            classification = 'spam'
        elif any(w in combined for w in ['partner', 'kooperation', 'zusammenarbeit', 'collaboration']):
            classification = 'partnership'
        elif any(w in combined for w in ['problem', 'fehler', 'error', 'hilfe', 'help', 'support', 'bug']):
            classification = 'support'
        elif any(w in combined for w in ['frage', 'question', 'wie', 'how', 'info', 'interesse', 'mitmachen']):
            classification = 'inquiry'
        else:
            classification = 'unknown'
        db.save_inbound_email(sender, subj, body, classification, msg_id)
        _relay_to_telegram('inbound_email', f"[{classification}] From: {sender}\nSubject: {subj}", 'ok')
    return jsonify({"ok": True})


# --- CEO Approval via Telegram ---
@app.route("/webhook/telegram", methods=['POST'])
@limiter.limit("20 per minute") if limiter else lambda f: f
def webhook_telegram():
    """Receive Telegram messages for CEO approve/deny flow."""
    import hmac as _hmac
    if not TELEGRAM_WEBHOOK_SECRET:
        abort(503)
    secret = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
    if not _hmac.compare_digest(secret, TELEGRAM_WEBHOOK_SECRET):
        abort(403)

    data = request.get_json(silent=True) or {}
    msg = data.get('message', {})
    chat_id = str(msg.get('chat', {}).get('id', ''))
    text = (msg.get('text') or '').strip()

    if not text or chat_id != TELEGRAM_CHAT_ID:
        return jsonify({"ok": True})

    parts = text.lower().split(None, 1)
    cmd = parts[0] if parts else ''

    # pending command
    if cmd == 'pending':
        decisions = db.get_ceo_decisions(status='pending')
        if not decisions:
            _send_telegram_message("No pending decisions.", reply_to_message_id=msg.get('message_id'))
        else:
            lines = [f"`{d['request_id']}` {d.get('activity','')} {d.get('summary','')[:60]}" for d in decisions[:10]]
            _send_telegram_message("Pending:\n" + "\n".join(lines), reply_to_message_id=msg.get('message_id'))
        return jsonify({"ok": True})

    # reset-lea command
    if cmd == 'reset-lea':
        db.set_lea_circuit_breaker(False)
        _send_telegram_message("\u2705 LEA circuit breaker reset. All budgeted tools restored.",
                               reply_to_message_id=msg.get('message_id'))
        return jsonify({"ok": True})

    # approve / deny
    if len(parts) < 2 or cmd not in ('approve', 'deny'):
        help_text = ("Commands:\n`approve <id>` approve a request\n`deny <id>` deny a request\n"
                     "`pending` list pending requests\n`reset-lea` reset LEA circuit breaker")
        _send_telegram_message(help_text, reply_to_message_id=msg.get('message_id'))
        return jsonify({"ok": True})

    action, request_id = cmd, parts[1].strip()
    status = 'approved' if action == 'approve' else 'denied'

    decision = db.resolve_ceo_decision(request_id, status)
    if not decision:
        _send_telegram_message(f"No pending request `{request_id}` found.",
                               reply_to_message_id=msg.get('message_id'))
        return jsonify({"ok": True})

    logger.info(f"[CEO] {status} request_id={request_id}")

    if status == 'approved':
        success, detail = _execute_approved_action(decision)
        if not success:
            db.update_ceo_decision_status(request_id, 'action_failed')
        reply = f"Approved `{request_id}`: {detail}"
    else:
        reply = f"Denied `{request_id}`."
        # Circuit breaker: 3+ denials in 24h trips it
        denial_count = db.count_recent_denials(hours=24)
        if denial_count >= 3:
            db.set_lea_circuit_breaker(True)
            reply += "\n\n\U0001f6d1 *Circuit breaker tripped.* 3+ denials in 24h. All budgeted LEA tools blocked. Send `reset-lea` to restore."

    _send_telegram_message(reply, reply_to_message_id=msg.get('message_id'))
    return jsonify({"ok": True})


@app.route("/api/internal/request-approval", methods=['POST'])
def api_internal_request_approval():
    """Create a CEO decision request and notify via Telegram."""
    token = request.headers.get('X-Internal-Token', '')
    if not INTERNAL_TOKEN or token != INTERNAL_TOKEN:
        abort(403)
    data = request.get_json(silent=True) or {}
    request_id = data.get('request_id', '').strip()
    activity = data.get('activity', '').strip()
    reference = data.get('reference', '')
    summary = data.get('summary', '')
    payload = data.get('payload', {})

    if not request_id or not activity:
        return jsonify({"error": "request_id and activity required"}), 400

    esc_ref = security_utils.escape_telegram_markdown(reference)
    esc_sum = security_utils.escape_telegram_markdown(summary)
    tg_text = (
        f"⚠️ *APPROVAL NEEDED*\n\n"
        f"*ID:* `{request_id}`\n"
        f"*Activity:* {activity}\n"
        f"*Reference:* {esc_ref}\n"
        f"*Summary:* {esc_sum}\n\n"
        f"Reply: `approve {request_id}` or `deny {request_id}`"
    )
    msg_id = _send_telegram_message(tg_text)

    inserted = db.create_ceo_decision(
        request_id=request_id, activity=activity,
        reference=reference, summary=summary,
        payload=payload, telegram_message_id=msg_id
    )
    if not inserted:
        return jsonify({"error": "duplicate request_id"}), 409
    return jsonify({"ok": True, "request_id": request_id, "status": "pending"})


# --- LEA Budget & Yellow Notification ---
@app.route("/api/internal/check-budget", methods=['POST'])
def api_internal_check_budget():
    """Check if LEA action is within budget. Fail-closed on error."""
    token = request.headers.get('X-Internal-Token', '')
    if not INTERNAL_TOKEN or token != INTERNAL_TOKEN:
        abort(403)
    # Circuit breaker check first
    if db.get_lea_circuit_breaker():
        return jsonify({"allowed": False, "reason": "circuit_breaker_tripped"})
    data = request.get_json(silent=True) or {}
    event_type = data.get('event_type', '')
    limit = data.get('limit')
    window = data.get('window', 86400)
    if not event_type or limit is None:
        return jsonify({"allowed": True, "used": 0, "limit": None, "window": window})
    used = db.count_budget_events(event_type, window)
    allowed = used < limit
    return jsonify({"allowed": allowed, "used": used, "limit": limit, "window": window})


@app.route("/api/internal/notify-yellow", methods=['POST'])
def api_internal_notify_yellow():
    """Send Telegram FYI for YELLOW tier actions."""
    token = request.headers.get('X-Internal-Token', '')
    if not INTERNAL_TOKEN or token != INTERNAL_TOKEN:
        abort(403)
    data = request.get_json(silent=True) or {}
    tool_name = security_utils.escape_telegram_markdown(data.get('tool_name', 'unknown'))
    summary = security_utils.escape_telegram_markdown(data.get('summary', '')[:500])
    tg_text = f"\U0001f7e1 *YELLOW ACTION*\n\n*Tool:* `{tool_name}`\n*Summary:* {summary}"
    _send_telegram_message(tg_text)
    db.track_event(f'lea_yellow_{data.get("tool_name", "unknown")}', data={'summary': data.get('summary', '')})
    return jsonify({"ok": True})


@app.route("/api/internal/notify-event", methods=['POST'])
def api_internal_notify_event():
    """Receive event notifications from MCP server, fire event hooks + Telegram."""
    token = request.headers.get('X-Internal-Token', '')
    if not INTERNAL_TOKEN or token != INTERNAL_TOKEN:
        abort(403)
    data = request.get_json(silent=True) or {}
    event_type = data.get('event_type', '')
    payload = data.get('payload', {})
    if not event_type:
        return jsonify({"error": "event_type required"}), 400
    event_hooks.fire(event_type, payload)
    safe_type = security_utils.escape_telegram_markdown(event_type)
    _send_telegram_message(f"\U0001f4e1 *Event:* `{safe_type}`\n{json.dumps(payload, default=str)[:300]}")
    db.track_event(f'event_{event_type}', data=payload)
    return jsonify({"ok": True})


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
                'num_members': len(member_list),
                'confidence_percent': float(ci.get('confidence_percent', 0) or 0),
                'profile_data_mix': ci.get('profile_data_mix') or 'mock',
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

    city_id = g.tenant.get('territory') if hasattr(g, 'tenant') else None
    db.track_event('funnel_address_check', data={'city_id': city_id, 'address_prefix': address[:20] if address else ''})
    cluster_info = find_provisional_matches(estimates, city_id=city_id)
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

    # Create unsubscribe + dashboard tokens
    unsub_token = str(uuid.uuid4())
    db.save_token(unsub_token, building_id, 'unsubscribe')
    unsubscribe_url = f"{APP_BASE_URL}/unsubscribe/{unsub_token}"
    dashboard_token = str(uuid.uuid4())
    db.save_token(dashboard_token, building_id, 'dashboard')

    # Background tasks
    threading.Thread(target=send_confirmation_email, args=(email, unsubscribe_url, building_id, profile.get('address', '')), daemon=True).start()
    threading.Thread(target=run_full_ml_task, args=(building_id, city_id), daemon=True).start()
    threading.Thread(target=email_automation.schedule_sequence_for_user, args=(building_id, email), daemon=True).start()

    # UTM params from frontend
    utm_data = {}
    for k in ('utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content'):
        v = (request.json.get(k) or '').strip()
        if v:
            utm_data[k] = v

    db.track_event('funnel_registration', building_id, {'type': 'anonymous', 'city_id': city_id, **utm_data})
    db.track_event('registration', building_id, {'type': 'anonymous', 'city_id': city_id})
    event_hooks.fire('registration', {'building_id': building_id, 'city_id': city_id, 'email': email, 'type': 'anonymous'})

    # Build response
    cluster_info = find_provisional_matches(profile, city_id=city_id)
    locations = collect_building_locations(city_id=city_id, exclude_building_id=building_id)
    referral_link = None
    ref_code = db.get_referral_code(building_id)
    if ref_code:
        referral_link = f"{APP_BASE_URL}/?ref={ref_code}"

    payload = {
        "buildings": locations,
        "match_found": bool(cluster_info),
        "verification_email_sent": True,
        "referral_link": referral_link,
        "dashboard_token": dashboard_token
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
    dashboard_token = str(uuid.uuid4())
    db.save_token(dashboard_token, building_id, 'dashboard')

    threading.Thread(target=send_confirmation_email, args=(email, unsubscribe_url, building_id, profile.get('address', '')), daemon=True).start()
    threading.Thread(target=run_full_ml_task, args=(building_id, city_id), daemon=True).start()
    threading.Thread(target=email_automation.schedule_sequence_for_user, args=(building_id, email), daemon=True).start()

    db.track_event('registration', building_id, {'type': 'registered', 'city_id': city_id})
    event_hooks.fire('registration', {'building_id': building_id, 'city_id': city_id, 'email': email, 'type': 'registered'})

    cluster_info = find_provisional_matches(profile, city_id=city_id)
    locations = collect_building_locations(city_id=city_id, exclude_building_id=building_id)
    referral_link = None
    ref_code = db.get_referral_code(building_id)
    if ref_code:
        referral_link = f"{APP_BASE_URL}/?ref={ref_code}"

    payload = {
        "buildings": locations,
        "match_found": bool(cluster_info),
        "verification_email_sent": True,
        "referral_link": referral_link,
        "dashboard_token": dashboard_token
    }
    if cluster_info:
        payload["cluster_info"] = cluster_info
    return jsonify(payload)


# --- Meter Data Upload ---
@app.route("/api/meter-data/upload", methods=['POST'])
@limiter.limit("10 per minute") if limiter else lambda f: f
def api_meter_data_upload():
    import meter_data
    # Verify dashboard token to authorize upload
    building_id = _require_dashboard_token()
    data = request.json or {}
    csv_content = data.get('csv_content', '')
    tier = int(data.get('tier', 1))

    if not csv_content:
        return jsonify({"error": "csv_content erforderlich."}), 400

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
            # Send unsubscribe link via email instead of deleting directly
            matches = db.get_building_by_email(email_value)
            if matches:
                for m in matches:
                    unsub_token = str(uuid.uuid4())
                    db.save_token(unsub_token, m['building_id'], 'unsubscribe')
                    unsub_url = f"{APP_BASE_URL}/unsubscribe/{unsub_token}"
                    threading.Thread(
                        target=send_email,
                        args=(email_value, f"{_tenant_name()}: Abmeldelink",
                              f"Klicken Sie auf den folgenden Link, um sich abzumelden:\n\n{unsub_url}"),
                        daemon=True
                    ).start()
            # Always show same message (don't reveal if email exists)
            status = "info"
            message = "Prüfen Sie Ihre E-Mail für den Abmeldelink."

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
    building_id = _require_dashboard_token()

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
@app.route("/api/referral/stats")
def api_referral_stats():
    building_id = _require_dashboard_token()
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


# --- Formation Lifecycle ---
@app.route("/api/formation/create", methods=['POST'])
def api_formation_create():
    import formation_wizard
    data = request.json or {}
    name = (data.get('name') or '').strip()
    building_id = (data.get('building_id') or '').strip()
    if not name or not building_id:
        return jsonify({"error": "name and building_id required"}), 400
    distribution_model = data.get('distribution_model', 'simple')
    description = data.get('description', '')
    result = formation_wizard.create_community(db, name, building_id, distribution_model, description)
    if not result:
        return jsonify({"error": "Failed to create community"}), 500
    return jsonify(result)


@app.route("/api/formation/invite", methods=['POST'])
def api_formation_invite():
    import formation_wizard
    data = request.json or {}
    community_id = (data.get('community_id') or '').strip()
    building_id = (data.get('building_id') or '').strip()
    invited_by = (data.get('invited_by') or '').strip()
    if not community_id or not building_id or not invited_by:
        return jsonify({"error": "community_id, building_id, invited_by required"}), 400
    ok = formation_wizard.invite_member(db, community_id, building_id, invited_by)
    if not ok:
        return jsonify({"error": "Invite failed"}), 400
    return jsonify({"success": True})


@app.route("/api/formation/confirm", methods=['POST'])
def api_formation_confirm():
    import formation_wizard
    data = request.json or {}
    community_id = (data.get('community_id') or '').strip()
    building_id = (data.get('building_id') or '').strip()
    if not community_id or not building_id:
        return jsonify({"error": "community_id and building_id required"}), 400
    ok = formation_wizard.confirm_membership(db, community_id, building_id)
    if not ok:
        return jsonify({"error": "Confirm failed"}), 400
    db.track_event('funnel_formation_confirm', building_id, {'community_id': community_id})
    return jsonify({"success": True})


@app.route("/api/formation/start", methods=['POST'])
def api_formation_start():
    import formation_wizard
    data = request.json or {}
    community_id = (data.get('community_id') or '').strip()
    if not community_id:
        return jsonify({"error": "community_id required"}), 400
    db.track_event('funnel_formation_start', data={'community_id': community_id})
    ok = formation_wizard.start_formation(db, community_id)
    if not ok:
        return jsonify({"error": "Formation start failed, check minimum members"}), 400
    return jsonify({"success": True})


@app.route("/api/formation/generate-docs", methods=['POST'])
def api_formation_generate_docs():
    import formation_wizard
    data = request.json or {}
    community_id = (data.get('community_id') or '').strip()
    if not community_id:
        return jsonify({"error": "community_id required"}), 400
    docs = formation_wizard.generate_documents(db, community_id)
    if not docs:
        return jsonify({"error": "Document generation failed"}), 500
    return jsonify(docs)


@app.route("/api/formation/submit-dso", methods=['POST'])
def api_formation_submit_dso():
    import formation_wizard
    data = request.json or {}
    community_id = (data.get('community_id') or '').strip()
    if not community_id:
        return jsonify({"error": "community_id required"}), 400
    ok = formation_wizard.submit_to_dso(db, community_id)
    if not ok:
        return jsonify({"error": "DSO submission failed"}), 400
    return jsonify({"success": True})


@app.route("/api/formation/status/<community_id>")
def api_formation_status(community_id):
    import formation_wizard
    status = formation_wizard.get_community_status(db, community_id)
    if not status:
        return jsonify({"error": "Community not found"}), 404
    return jsonify(status)


# --- Cron ---
@app.route("/api/cron/process-emails", methods=['POST'])
def api_cron_process_emails():
    _require_cron_secret()
    result = email_automation.process_email_queue(app=app)
    # Process formation nudges for communities ready to form
    nudge_count = 0
    try:
        ready = email_automation.check_formation_ready_communities()
        for community in ready:
            emails = community.get('member_emails', [])
            if emails:
                sent = email_automation.send_formation_nudge(
                    community['community_id'],
                    community['name'],
                    emails,
                    app=app,
                )
                if sent > 0:
                    db.track_event('formation_nudge_sent', data={'community_id': community['community_id']})
                    nudge_count += 1
    except Exception as e:
        logger.warning(f"[CRON] Formation nudge error: {e}")
    result['nudges_sent'] = nudge_count
    db.expire_stale_ceo_decisions()
    return jsonify(result)


@app.route("/api/cron/refresh-public-data", methods=['POST'])
def api_cron_refresh_public_data():
    _require_cron_secret()
    import public_data
    data = request.get_json(silent=True) or {}
    scope = data.get('scope', 'zh').strip().upper()
    if scope == 'ALL':
        result = public_data.refresh_all_municipalities()
    else:
        kanton = scope if len(scope) == 2 and scope.isalpha() else 'ZH'
        result = public_data.refresh_canton(kanton, year=int(data.get('year', 2026)))
    return jsonify(result)



@app.route("/api/email/stats")
def api_email_stats():
    _require_admin()
    return jsonify(db.get_email_stats())


# --- Webhooks ---


@app.route("/webhook/deepsign", methods=['POST'])
def webhook_deepsign():
    """Handle DeepSign e-signature webhook callbacks (HMAC verified)."""
    import deepsign_integration
    raw_body = request.get_data()
    signature = request.headers.get('X-DeepSign-Signature', '')
    if not deepsign_integration.verify_webhook_signature(raw_body, signature):
        log_security_event("WEBHOOK_SIGNATURE_INVALID", "DeepSign webhook failed verification", 'WARNING')
        abort(403)
    payload = request.get_json(silent=True) or {}
    result = deepsign_integration.handle_webhook(payload)
    logger.info(f"[DEEPSIGN] Webhook: {result.get('action')} for {result.get('document_id')}")
    return jsonify(result), 200


# --- Billing Cron ---
@app.route("/api/cron/process-billing", methods=['POST'])
def api_cron_process_billing():
    _require_cron_secret()
    import billing_engine
    from datetime import datetime, timedelta
    communities = db.get_active_communities()
    processed = 0
    period_end = datetime.now()
    period_start = period_end - timedelta(days=30)
    for community in communities:
        cid = community['community_id']
        building_ids = db.get_community_member_building_ids(cid)
        if not building_ids:
            continue
        # Build timestamp-aligned production series and consumption DataFrame
        consumption_frames = {}
        production_frames = {}
        for bid in building_ids:
            readings = db.get_meter_readings(bid, start=period_start, end=period_end, limit=100000)
            if not readings:
                continue
            for r in readings:
                ts = r.get('timestamp')
                if ts is None:
                    continue
                consumption_frames.setdefault(ts, {})[bid] = r.get('consumption_kwh', 0) or 0
                production_frames[ts] = production_frames.get(ts, 0) + (r.get('production_kwh', 0) or 0)
        if not consumption_frames:
            continue
        # Build timestamp-indexed DataFrames
        timestamps = sorted(consumption_frames.keys())
        consumption_df = pd.DataFrame([
            {bid: consumption_frames[ts].get(bid, 0) for bid in building_ids if bid in consumption_frames.get(ts, {})}
            for ts in timestamps
        ]).fillna(0)
        production_series = pd.Series([production_frames.get(ts, 0) for ts in timestamps])
        model = community.get('distribution_model', 'proportional')
        grid_fee = 0.09  # Default 9 Rp/kWh
        internal_price = 0.15  # Default 15 Rp/kWh
        summary = billing_engine.generate_billing_summary(
            production_series, consumption_df,
            grid_fee, internal_price,
            network_level="same",
            distribution_model=model,
        )
        db.save_billing_period(cid, period_start, period_end, summary)
        processed += 1
    return jsonify({"processed": processed, "communities": len(communities)})


@app.route("/api/cron/seed-municipalities", methods=['POST'])
def api_cron_seed_municipalities():
    _require_cron_secret()
    kanton = request.args.get('kanton', '').strip() or None
    provision_tenants = request.args.get('provision_tenants', '').lower() == 'true'
    result = municipality_seeder.seed_all_municipalities(
        kanton_filter=kanton, provision_tenants=provision_tenants)
    return jsonify(result)


@app.route("/api/cron/process-municipality-outreach", methods=['POST'])
def api_cron_process_municipality_outreach():
    _require_cron_secret()
    import email_automation
    data = request.get_json(silent=True) or {}
    schedule_new = data.get('schedule_new', False)
    if schedule_new:
        scheduled = email_automation.schedule_outreach_batch(limit=int(data.get('limit', 10)))
    else:
        scheduled = 0
    result = email_automation.process_municipality_outreach(app)
    result['newly_scheduled'] = scheduled
    followup_1_count = email_automation.schedule_municipality_followups(followup_number=1, days_after=7)
    followup_2_count = email_automation.schedule_municipality_followups(followup_number=2, days_after=14)
    result['followups_scheduled'] = followup_1_count + followup_2_count
    return jsonify(result)


@app.route("/api/cron/monitor-formations", methods=['POST'])
def api_cron_monitor_formations():
    _require_cron_secret()
    from email_automation import monitor_formation_pipeline, send_formation_nudge
    pipeline = monitor_formation_pipeline()
    nudges_sent = 0
    for community in pipeline.get('stuck', []):
        sent = send_formation_nudge(
            community['community_id'],
            community['name'],
            community.get('member_emails', []),
            app=app,
        )
        nudges_sent += sent
    db.track_event('pipeline_monitor_run', data={
        'total': pipeline.get('total_communities', 0),
        'stuck': len(pipeline.get('stuck', [])),
        'nudges_sent': nudges_sent,
    })
    summary = f"Pipeline: {pipeline.get('total_communities', 0)} communities, {len(pipeline.get('stuck', []))} stuck, {nudges_sent} nudges sent"
    _relay_to_telegram('formation_monitor', summary, 'ok')
    return jsonify({"pipeline": pipeline, "nudges_sent": nudges_sent})


@app.route("/api/billing/community/<community_id>/period/<int:period_id>")
def api_billing_period(community_id, period_id):
    _require_admin()
    period = db.get_billing_period(period_id)
    if not period:
        return jsonify({"error": "Period not found"}), 404
    return jsonify(period)


# --- Metrics ---
@app.route("/metrics")
def metrics():
    stats = db.get_stats()
    communities = db.get_active_communities()
    return jsonify({
        "active_communities": len(communities),
        "total_buildings": stats.get('total_buildings', 0),
        "registrations_today": stats.get('registrations_today', 0),
    })


if __name__ == "__main__":
    app.run(debug=True, port=5003, host='127.0.0.1')
