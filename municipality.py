"""
Municipality onboarding for OpenLEG platform.
Handles Gemeinde signup, admin dashboard, LEG formation KPIs.
Public profile pages and directory for municipalities.
"""
import logging
import os
import re
from typing import Dict, Optional
from flask import Blueprint, request, jsonify, render_template, g, abort

import database as db
import security_utils
import tenant as tenant_module

logger = logging.getLogger(__name__)

municipality_bp = Blueprint('municipality', __name__, url_prefix='/gemeinde')

# Zurich canton municipalities (key candidates for initial outreach)
ZURICH_MUNICIPALITIES = [
    {"bfs": 261, "name": "Dietikon", "population": 29000, "dso": "EKZ"},
    {"bfs": 247, "name": "Schlieren", "population": 20000, "dso": "EKZ"},
    {"bfs": 242, "name": "Urdorf", "population": 10500, "dso": "EKZ"},
    {"bfs": 230, "name": "Winterthur", "population": 115000, "dso": "Stadtwerk Winterthur"},
    {"bfs": 159, "name": "Wädenswil", "population": 25000, "dso": "EKZ"},
    {"bfs": 295, "name": "Horgen", "population": 23000, "dso": "EKZ"},
    {"bfs": 191, "name": "Dübendorf", "population": 30000, "dso": "EKZ"},
    {"bfs": 62, "name": "Kloten", "population": 20000, "dso": "EKZ"},
    {"bfs": 66, "name": "Opfikon", "population": 21000, "dso": "EKZ"},
    {"bfs": 53, "name": "Bülach", "population": 22000, "dso": "EKZ"},
    {"bfs": 198, "name": "Uster", "population": 36000, "dso": "EKZ"},
    {"bfs": 296, "name": "Illnau-Effretikon", "population": 22000, "dso": "EKZ"},
]


def _is_demo_mode_enabled() -> bool:
    return os.getenv("DEMO_MODE", "false").strip().lower() in ("1", "true", "yes", "on")


def _demo_subdomain() -> str:
    raw = os.getenv("DEMO_SUBDOMAIN", "newbaden").strip().lower()
    slug = re.sub(r"[^a-z0-9-]", "", raw).strip("-")
    return slug or "newbaden"


def _demo_env() -> str:
    return os.getenv("DEMO_ENV", "staging").strip().lower() or "staging"


def _demo_url() -> str:
    return f"https://{_demo_subdomain()}.openleg.ch"


def _to_int(value, default: Optional[int] = None) -> Optional[int]:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_payload(payload: Dict) -> Dict:
    municipality_name = security_utils.sanitize_string(payload.get("municipality_name", ""), max_length=80)
    contact_name = security_utils.sanitize_string(payload.get("contact_name", ""), max_length=80)
    contact_email = (payload.get("contact_email") or "").strip()
    kanton = security_utils.sanitize_string(payload.get("kanton", ""), max_length=60) or "Aargau"
    kanton_code = security_utils.sanitize_string(payload.get("kanton_code", ""), max_length=8).upper() or "AG"
    dso_name = security_utils.sanitize_string(payload.get("dso_name", ""), max_length=120) or "Regionalwerke Baden"
    population = _to_int(payload.get("population"), default=22000)

    if not municipality_name or not contact_name or not contact_email:
        raise ValueError("Gemeinde, Ansprechperson und E-Mail sind erforderlich.")

    is_valid, normalized, error = security_utils.validate_email_address(contact_email)
    if not is_valid:
        raise ValueError(error)

    return {
        "municipality_name": municipality_name,
        "contact_name": contact_name,
        "contact_email": normalized,
        "kanton": kanton,
        "kanton_code": kanton_code[:2],
        "dso_name": dso_name,
        "population": population,
    }


def build_demo_tenant_config(payload: Dict) -> Dict:
    municipality_name = payload["municipality_name"]
    utility_name = payload["dso_name"]
    return {
        "territory": _demo_subdomain(),
        "utility_name": utility_name,
        "primary_color": "#0f766e",
        "secondary_color": "#d97706",
        "contact_email": payload["contact_email"],
        "legal_entity": f"Einwohnergemeinde {municipality_name}",
        "dso_contact": utility_name,
        "active": True,
        "city_name": municipality_name,
        "kanton": payload["kanton"],
        "kanton_code": payload["kanton_code"],
        "platform_name": f"{municipality_name} OpenLEG",
        "brand_prefix": municipality_name,
        "map_center_lat": 47.4767,
        "map_center_lon": 8.3065,
        "map_zoom": 13,
        "map_bounds_sw": [47.42, 8.24],
        "map_bounds_ne": [47.52, 8.38],
        "plz_ranges": [[5400, 5408]],
        "solar_kwh_per_kwp": 1000,
        "site_url": _demo_url(),
        "ga4_id": "",
    }


def seed_demo_content(territory: str, payload: Dict) -> Dict:
    bfs_number = _to_int(os.getenv("DEMO_BFS_NUMBER"), default=79999)
    municipality_id = db.save_municipality(
        bfs_number=bfs_number,
        name=payload["municipality_name"],
        kanton=payload["kanton_code"],
        dso_name=payload["dso_name"],
        population=payload["population"],
        subdomain=territory,
    )
    if not municipality_id:
        raise RuntimeError("Demo-Gemeinde konnte nicht gespeichert werden.")

    db.update_municipality_status(bfs_number, "demo_ready", admin_email=payload["contact_email"])
    db.save_municipality_profile({
        "bfs_number": bfs_number,
        "name": payload["municipality_name"],
        "kanton": payload["kanton_code"],
        "population": payload["population"],
        "solar_potential_pct": 48.5,
        "solar_installed_kwp": 12200,
        "ev_share_pct": 19.5,
        "renewable_heating_pct": 42.0,
        "electricity_consumption_mwh": 158000,
        "renewable_production_mwh": 23600,
        "leg_value_gap_chf": 178.0,
        "energy_transition_score": 51.0,
        "data_sources": {"demo": True},
    })

    return {"municipality_id": municipality_id, "bfs_number": bfs_number}


def provision_demo_instance(payload: Dict) -> Dict:
    territory = _demo_subdomain()
    cleaned = _clean_payload(payload or {})
    existing = db.get_tenant_by_territory(territory)

    if not db.upsert_tenant(territory, build_demo_tenant_config(cleaned)):
        raise RuntimeError("Tenant-Konfiguration konnte nicht gespeichert werden.")

    seeded = seed_demo_content(territory, cleaned)
    tenant_module.invalidate_cache(territory)
    db.track_event("demo_instance_provisioned", data={
        "territory": territory,
        "municipality_name": cleaned["municipality_name"],
        "contact_email": cleaned["contact_email"],
        "already_exists": bool(existing),
    })

    return {
        "success": True,
        "already_exists": bool(existing),
        "demo_subdomain": territory,
        "demo_url": _demo_url(),
        "environment": _demo_env(),
        "tenant_ready": True,
        "municipality_id": seeded["municipality_id"],
        "bfs_number": seeded["bfs_number"],
    }


@municipality_bp.route('/onboarding')
def onboarding():
    return render_template(
        'gemeinde/onboarding.html',
        municipalities=ZURICH_MUNICIPALITIES,
        demo_enabled=_is_demo_mode_enabled(),
        demo_subdomain=_demo_subdomain(),
        demo_env=_demo_env(),
        demo_url=_demo_url(),
    )

@municipality_bp.route('/register', methods=['POST'])
def register():
    data = request.json or {}
    bfs = data.get('bfs_number')
    name = data.get('name', '').strip()
    admin_email = data.get('admin_email', '').strip()

    if not bfs or not name or not admin_email:
        return jsonify({"error": "BFS-Nummer, Name und E-Mail erforderlich."}), 400

    is_valid, normalized, error = security_utils.validate_email_address(admin_email)
    if not is_valid:
        return jsonify({"error": error}), 400

    muni = next((m for m in ZURICH_MUNICIPALITIES if m['bfs'] == int(bfs)), None)
    dso = muni['dso'] if muni else 'EKZ'
    population = muni['population'] if muni else None

    subdomain = name.lower().replace(' ', '-').replace('ü', 'ue').replace('ä', 'ae').replace('ö', 'oe')

    muni_id = db.save_municipality(
        bfs_number=int(bfs), name=name, kanton='ZH',
        dso_name=dso, population=population, subdomain=subdomain
    )

    if muni_id:
        db.update_municipality_status(int(bfs), 'registered', admin_email=normalized)
        db.track_event('municipality_registered', data={'bfs': bfs, 'name': name})
        return jsonify({"success": True, "municipality_id": muni_id, "subdomain": subdomain})

    return jsonify({"error": "Registrierung fehlgeschlagen."}), 500


@municipality_bp.route('/demo/status')
def demo_status():
    territory = _demo_subdomain()
    if not _is_demo_mode_enabled():
        return jsonify({
            "enabled": False,
            "ready": False,
            "demo_subdomain": territory,
            "demo_url": _demo_url(),
            "environment": _demo_env(),
        }), 503

    tenant_row = db.get_tenant_by_territory(territory)
    ready = bool(tenant_row and tenant_row.get("active", True))
    return jsonify({
        "enabled": True,
        "ready": ready,
        "demo_subdomain": territory,
        "demo_url": _demo_url(),
        "environment": _demo_env(),
        "tenant_exists": bool(tenant_row),
    })


@municipality_bp.route('/demo/provision', methods=['POST'])
def demo_provision():
    if not _is_demo_mode_enabled():
        return jsonify({"error": "Demo-Modus ist deaktiviert."}), 403

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON-Body erforderlich."}), 400

    try:
        return jsonify(provision_demo_instance(payload))
    except ValueError as err:
        return jsonify({"error": str(err)}), 400
    except Exception as err:
        logger.error(f"[DEMO] Provisioning failed: {err}")
        return jsonify({"error": "Demo-Instanz konnte nicht erstellt werden."}), 500


@municipality_bp.route('/dashboard')
def dashboard():
    subdomain = request.args.get('subdomain', '').strip()
    bfs = request.args.get('bfs', '')

    muni = None
    if subdomain:
        muni = db.get_municipality(subdomain=subdomain)
    elif bfs:
        muni = db.get_municipality(bfs_number=int(bfs))

    if not muni:
        return render_template('gemeinde/dashboard.html', municipality=None, error="Gemeinde nicht gefunden.")

    stats = db.get_stats(city_id=muni.get('subdomain'))
    return render_template('gemeinde/dashboard.html', municipality=muni, stats=stats, error=None)

@municipality_bp.route('/api/municipalities')
def api_municipalities():
    # Try DB profiles first, fall back to hardcoded list
    profiles = db.get_all_municipality_profiles(kanton='ZH')
    if profiles:
        return jsonify({"municipalities": [
            {"bfs": p.get('bfs_number'), "name": p.get('name', ''),
             "population": p.get('population'), "score": float(p.get('energy_transition_score', 0) or 0)}
            for p in profiles
        ]})
    return jsonify({"municipalities": ZURICH_MUNICIPALITIES})


# === Public Profile Pages ===

@municipality_bp.route('/profil/<int:bfs>')
def profil(bfs):
    """Public municipality profile page with energy data visualization."""
    profile = db.get_municipality_profile(bfs)
    if not profile:
        # Check hardcoded list as fallback
        muni = next((m for m in ZURICH_MUNICIPALITIES if m['bfs'] == bfs), None)
        if not muni:
            abort(404)
        profile = {
            'bfs_number': bfs, 'name': muni['name'], 'kanton': 'ZH',
            'population': muni['population'], 'energy_transition_score': 0,
        }

    tariffs = db.get_elcom_tariffs(bfs, year=2026)
    solar = db.get_sonnendach_municipal(bfs)

    # Compute value gap if H4 tariff available
    import public_data
    h4 = next((t for t in tariffs if str(t.get('category', '')).startswith('H4')), None)
    value_gap = public_data.compute_leg_value_gap(h4) if h4 else None

    return render_template('gemeinde/profil.html',
        profile=profile, tariffs=tariffs, solar=solar,
        value_gap=value_gap, h4_tariff=h4)


@municipality_bp.route('/verzeichnis')
def verzeichnis():
    """Searchable municipality directory."""
    kanton = request.args.get('kanton', 'ZH')
    order_by = request.args.get('sort', 'energy_transition_score')
    q = request.args.get('q', '').strip()

    profiles = db.get_all_municipality_profiles(kanton=kanton, order_by=order_by)
    # Reverse for descending score/gap
    if order_by in ('energy_transition_score', 'leg_value_gap_chf', 'population'):
        profiles = list(reversed(profiles))

    if q:
        profiles = [p for p in profiles if q.lower() in (p.get('name', '') or '').lower()]

    return render_template('gemeinde/verzeichnis.html',
        profiles=profiles, kanton=kanton, query=q, sort=order_by)
