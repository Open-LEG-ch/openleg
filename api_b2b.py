"""
OpenLEG B2B API Blueprint.
Serves aggregated energy intelligence to API clients (energy providers).
"""
import hashlib
import logging
from functools import wraps
from flask import Blueprint, request, jsonify, g

import database as db
import insights_engine

logger = logging.getLogger(__name__)

b2b_bp = Blueprint('b2b', __name__, url_prefix='/api/insights')

# Tier access levels
TIER_ACCESS = {
    'starter': ['load_profiles', 'solar_index'],
    'professional': ['load_profiles', 'solar_index', 'flexibility', 'community_signals'],
    'enterprise': ['load_profiles', 'solar_index', 'flexibility', 'community_signals', 'raw_export'],
}


def require_api_key(f):
    """Authenticate B2B API requests via API key."""
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get('X-API-Key', '')
        if not api_key:
            return jsonify({"error": "API key required. Pass via X-API-Key header."}), 401

        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        client = db.get_api_client_by_key(key_hash)

        if not client:
            return jsonify({"error": "Invalid API key."}), 403

        # Check rate limit
        usage_count = db.get_api_usage_count(client['id'], hours=1)
        if usage_count >= client.get('rate_limit_per_hour', 100):
            return jsonify({"error": "Rate limit exceeded.", "retry_after_seconds": 3600}), 429

        g.api_client = client
        return f(*args, **kwargs)
    return decorated


def _check_tier_access(endpoint_name: str) -> bool:
    """Check if client tier allows access to this endpoint."""
    client = getattr(g, 'api_client', None)
    if not client:
        return False
    tier = client.get('tier', 'starter')
    allowed = TIER_ACCESS.get(tier, [])
    return endpoint_name in allowed


def _track_and_respond(endpoint: str, data: dict, params: dict = None):
    """Track API usage and return response."""
    import json
    client = getattr(g, 'api_client', {})
    response_json = json.dumps(data)
    db.track_api_usage(client.get('id'), endpoint, params=params, response_size=len(response_json))
    return jsonify(data)


@b2b_bp.route('/load-profiles')
@require_api_key
def load_profiles():
    """B2B: Consumption patterns by PLZ, building type, time-of-day."""
    if not _check_tier_access('load_profiles'):
        return jsonify({"error": "Upgrade your plan for access to this endpoint."}), 403

    plz = request.args.get('plz')
    period = request.args.get('period', 'month')

    # Try cache first
    cached = db.get_insight('load_profiles', scope=plz or 'ZH', period=period)
    if cached:
        return _track_and_respond('/api/insights/load-profiles', cached['data'], {'plz': plz})

    data = insights_engine.compute_load_profiles(plz=plz, period=period)
    return _track_and_respond('/api/insights/load-profiles', data, {'plz': plz})


@b2b_bp.route('/solar-index')
@require_api_key
def solar_index():
    """B2B: PV penetration and battery storage rates by municipality."""
    if not _check_tier_access('solar_index'):
        return jsonify({"error": "Upgrade your plan for access to this endpoint."}), 403

    kanton = request.args.get('kanton', 'ZH')

    cached = db.get_insight('solar_index', scope=kanton)
    if cached:
        return _track_and_respond('/api/insights/solar-index', cached['data'], {'kanton': kanton})

    data = insights_engine.compute_solar_index(kanton=kanton)
    return _track_and_respond('/api/insights/solar-index', data, {'kanton': kanton})


@b2b_bp.route('/flexibility')
@require_api_key
def flexibility():
    """B2B: Demand response potential (heat pumps, EVs)."""
    if not _check_tier_access('flexibility'):
        return jsonify({"error": "Upgrade your plan for access to this endpoint."}), 403

    cached = db.get_insight('flexibility')
    if cached:
        return _track_and_respond('/api/insights/flexibility', cached['data'])

    data = insights_engine.compute_flexibility_potential()
    return _track_and_respond('/api/insights/flexibility', data)


@b2b_bp.route('/community-signals')
@require_api_key
def community_signals():
    """B2B: Early indicators of organizing neighborhoods."""
    if not _check_tier_access('community_signals'):
        return jsonify({"error": "Upgrade your plan for access to this endpoint."}), 403

    cached = db.get_insight('community_signals')
    if cached:
        return _track_and_respond('/api/insights/community-signals', cached['data'])

    data = insights_engine.compute_community_signals()
    return _track_and_respond('/api/insights/community-signals', data)


@b2b_bp.route('/status')
@require_api_key
def api_status():
    """B2B: Check API status and client info."""
    client = g.api_client
    usage = db.get_api_usage_count(client['id'], hours=1)

    return jsonify({
        "status": "active",
        "company": client.get('company_name'),
        "tier": client.get('tier'),
        "rate_limit": client.get('rate_limit_per_hour'),
        "usage_this_hour": usage,
        "allowed_cantons": client.get('allowed_cantons', ['ZH']),
        "available_endpoints": TIER_ACCESS.get(client.get('tier', 'starter'), [])
    })
