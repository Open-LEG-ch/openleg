"""
Municipality onboarding for OpenLEG platform.
Handles Gemeinde signup, admin dashboard, LEG formation KPIs.
Public profile pages and directory for municipalities.
"""
import logging
from flask import Blueprint, request, jsonify, render_template, g, abort

import database as db
import security_utils

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

@municipality_bp.route('/onboarding')
def onboarding():
    return render_template('gemeinde/onboarding.html', municipalities=ZURICH_MUNICIPALITIES)

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
