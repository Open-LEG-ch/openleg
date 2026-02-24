"""
Multi-tenant resolution for OpenLEG platform.
Maps hostnames to territory configs stored in white_label_configs table.
"""
import time
import logging
from typing import Dict, Optional
from flask import g, request

logger = logging.getLogger(__name__)

# In-memory cache: territory -> (config_dict, fetched_at)
_tenant_cache: Dict[str, tuple] = {}
CACHE_TTL_SECONDS = 300  # 5 min

DEFAULT_TENANT = {
    "territory": "zurich",
    "city_name": "Zürich",
    "kanton": "Zürich",
    "kanton_code": "ZH",
    "platform_name": "OpenLEG",
    "brand_prefix": "OpenLEG",
    "utility_name": "EKZ",
    "dso_name": "EKZ",
    "grid_level": "NE7",
    "tariff_group": "standard",
    "primary_color": "#6366f1",
    "secondary_color": "#f59e0b",
    "contact_email": "hallo@openleg.ch",
    "contact_phone": "",
    "legal_entity": "",
    "dso_contact": "EKZ Verteilnetz AG",
    "map_center_lat": 47.3769,
    "map_center_lon": 8.5417,
    "map_zoom": 12,
    "map_bounds_sw": [47.20, 8.30],
    "map_bounds_ne": [47.60, 8.80],
    "plz_ranges": [[8000, 8999]],
    "solar_kwh_per_kwp": 1000,
    "site_url": "",
    "ga4_id": "",
    "active": True,
}


def resolve_tenant(hostname: str) -> str:
    """Extract territory slug from hostname.

    dietikon.openleg.ch -> dietikon
    openleg.ch / www.openleg.ch / localhost -> zurich
    """
    if not hostname:
        return "zurich"

    hostname = hostname.lower().split(":")[0]  # strip port

    # Skip known non-tenant subdomains
    skip = {"www", "openclaw", "api", "admin", "insights"}

    if hostname in ("openleg.ch", "www.openleg.ch", "localhost", "127.0.0.1"):
        return "zurich"

    # Check for subdomain pattern: <territory>.openleg.ch
    if hostname.endswith(".openleg.ch"):
        sub = hostname.replace(".openleg.ch", "")
        if sub and sub not in skip:
            return sub

    return "zurich"


def get_tenant_config(territory: str, db=None) -> Dict:
    """Load tenant config from DB with in-memory cache. Falls back to DEFAULT_TENANT."""
    now = time.time()

    # Check cache
    if territory in _tenant_cache:
        cached, fetched_at = _tenant_cache[territory]
        if now - fetched_at < CACHE_TTL_SECONDS:
            return cached

    # Try DB lookup
    if db is not None:
        try:
            row = _load_tenant_from_db(territory, db)
            if row:
                config = _merge_tenant_row(row)
                _tenant_cache[territory] = (config, now)
                return config
        except Exception as e:
            logger.warning(f"[TENANT] DB lookup failed for {territory}: {e}")

    # Fallback: if territory is zurich, return defaults
    if territory == "zurich":
        _tenant_cache[territory] = (DEFAULT_TENANT.copy(), now)
        return DEFAULT_TENANT.copy()

    # Unknown territory: return defaults with territory name substituted
    fallback = DEFAULT_TENANT.copy()
    fallback["territory"] = territory
    fallback["city_name"] = territory.capitalize()
    fallback["platform_name"] = "OpenLEG"
    fallback["brand_prefix"] = "OpenLEG"
    _tenant_cache[territory] = (fallback, now)
    return fallback


def _load_tenant_from_db(territory: str, db) -> Optional[Dict]:
    """Load tenant row from white_label_configs."""
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT territory, utility_name, logo_url, primary_color,
                           secondary_color, contact_email, contact_phone,
                           legal_entity, dso_contact, active, config
                    FROM white_label_configs
                    WHERE territory = %s AND active = TRUE
                """, (territory,))
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception:
        return None


def _merge_tenant_row(row: Dict) -> Dict:
    """Merge DB row + JSONB config into a flat tenant dict."""
    config = DEFAULT_TENANT.copy()

    # Direct column mappings
    if row.get("territory"):
        config["territory"] = row["territory"]
    if row.get("utility_name"):
        config["utility_name"] = row["utility_name"]
    if row.get("primary_color"):
        config["primary_color"] = row["primary_color"]
    if row.get("secondary_color"):
        config["secondary_color"] = row["secondary_color"]
    if row.get("contact_email"):
        config["contact_email"] = row["contact_email"]
    if row.get("contact_phone"):
        config["contact_phone"] = row["contact_phone"]
    if row.get("legal_entity"):
        config["legal_entity"] = row["legal_entity"]
    if row.get("dso_contact"):
        config["dso_contact"] = row["dso_contact"]
    config["active"] = row.get("active", True)

    # JSONB config overrides everything
    jsonb = row.get("config") or {}
    if isinstance(jsonb, dict):
        for key in ("city_name", "kanton", "kanton_code", "platform_name",
                     "brand_prefix", "map_center_lat", "map_center_lon",
                     "map_zoom", "map_bounds_sw", "map_bounds_ne",
                     "plz_ranges", "solar_kwh_per_kwp", "site_url", "ga4_id",
                     "dso_name", "grid_level", "tariff_group"):
            if key in jsonb:
                config[key] = jsonb[key]

    return config


def invalidate_cache(territory: Optional[str] = None):
    """Clear tenant cache. Pass territory for single entry, None for all."""
    if territory:
        _tenant_cache.pop(territory, None)
    else:
        _tenant_cache.clear()


def init_tenant_middleware(app, db=None):
    """Register before_request hook and context processor."""

    @app.before_request
    def _set_tenant():
        hostname = request.host or ""
        territory = resolve_tenant(hostname)
        g.tenant = get_tenant_config(territory, db=db)

    @app.context_processor
    def _inject_tenant():
        tenant = getattr(g, "tenant", DEFAULT_TENANT)
        return {
            "tenant": tenant,
            "city_name": tenant.get("city_name", "Zürich"),
            "kanton": tenant.get("kanton", "Zürich"),
            "kanton_code": tenant.get("kanton_code", "ZH"),
            "platform_name": tenant.get("platform_name", "OpenLEG"),
            "brand_prefix": tenant.get("brand_prefix", "OpenLEG"),
            "utility_name": tenant.get("utility_name", "EKZ"),
            "primary_color": tenant.get("primary_color", "#6366f1"),
            "secondary_color": tenant.get("secondary_color", "#f59e0b"),
            "contact_email": tenant.get("contact_email", "hallo@openleg.ch"),
            "dso_contact": tenant.get("dso_contact", "EKZ Verteilnetz AG"),
            "legal_entity": tenant.get("legal_entity", ""),
            "map_center_lat": tenant.get("map_center_lat", 47.3769),
            "map_center_lon": tenant.get("map_center_lon", 8.5417),
            "map_zoom": tenant.get("map_zoom", 12),
            "map_bounds_sw": tenant.get("map_bounds_sw", [47.20, 8.30]),
            "map_bounds_ne": tenant.get("map_bounds_ne", [47.60, 8.80]),
            "solar_kwh_per_kwp": tenant.get("solar_kwh_per_kwp", 1000),
        }
