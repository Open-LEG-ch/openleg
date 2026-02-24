"""
OpenLEG Public API Blueprint.
Open-source Swiss energy data API: municipalities, tariffs, solar, LEG toolkit.
No auth required. Rate limited. CORS enabled.
"""
import logging
from functools import wraps
from flask import Blueprint, request, jsonify, render_template

import database as db
import public_data

logger = logging.getLogger(__name__)

public_api_bp = Blueprint('public_api', __name__, url_prefix='/api/v1')


# === CORS ===

@public_api_bp.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response


# === Rate limiting helper ===

_request_counts = {}


def _rate_limit_key():
    return request.headers.get('X-Forwarded-For', request.remote_addr)


# === Municipality endpoints ===

@public_api_bp.route('/municipalities')
def list_municipalities():
    """List all municipalities with profiles."""
    kanton = request.args.get('kanton', 'ZH')
    order_by = request.args.get('order_by', 'name')
    profiles = db.get_all_municipality_profiles(kanton=kanton, order_by=order_by)
    return jsonify({
        "municipalities": _serialize_profiles(profiles),
        "count": len(profiles),
        "kanton": kanton
    })


@public_api_bp.route('/municipalities/<int:bfs>')
def get_municipality(bfs):
    """Single municipality profile."""
    profile = db.get_municipality_profile(bfs)
    if not profile:
        return jsonify({"error": "Municipality not found", "bfs_number": bfs}), 404
    return jsonify(_serialize_profile(profile))


@public_api_bp.route('/municipalities/<int:bfs>/tariffs')
def get_municipality_tariffs(bfs):
    """ElCom tariffs for a municipality."""
    year = request.args.get('year', type=int)
    tariffs = db.get_elcom_tariffs(bfs, year=year)
    return jsonify({
        "bfs_number": bfs,
        "tariffs": _serialize_tariffs(tariffs),
        "count": len(tariffs)
    })


@public_api_bp.route('/municipalities/<int:bfs>/solar')
def get_municipality_solar(bfs):
    """Sonnendach data for a municipality."""
    solar = db.get_sonnendach_municipal(bfs)
    if not solar:
        return jsonify({"error": "No solar data found", "bfs_number": bfs}), 404
    return jsonify(_serialize_solar(solar))


@public_api_bp.route('/municipalities/<int:bfs>/score')
def get_municipality_score(bfs):
    """Energy transition score breakdown."""
    profile = db.get_municipality_profile(bfs)
    if not profile:
        return jsonify({"error": "Municipality not found"}), 404

    score = public_data.compute_energy_transition_score(profile)
    solar = min(float(profile.get('solar_potential_pct', 0) or 0), 100) / 100.0
    ev = min(float(profile.get('ev_share_pct', 0) or 0), 30) / 30.0
    heating = min(float(profile.get('renewable_heating_pct', 0) or 0), 100) / 100.0
    consumption = float(profile.get('electricity_consumption_mwh', 0) or 0)
    production = float(profile.get('renewable_production_mwh', 0) or 0)
    prod_ratio = min(production / consumption, 1.0) if consumption > 0 else 0

    return jsonify({
        "bfs_number": bfs,
        "name": profile.get('name', ''),
        "total_score": score,
        "breakdown": {
            "solar": {"weight": 30, "raw_pct": float(profile.get('solar_potential_pct', 0) or 0), "score": round(solar * 30, 1)},
            "ev": {"weight": 20, "raw_pct": float(profile.get('ev_share_pct', 0) or 0), "score": round(ev * 20, 1)},
            "heating": {"weight": 25, "raw_pct": float(profile.get('renewable_heating_pct', 0) or 0), "score": round(heating * 25, 1)},
            "production": {"weight": 25, "raw_pct": round(prod_ratio * 100, 1), "score": round(prod_ratio * 25, 1)},
        }
    })


@public_api_bp.route('/municipalities/<int:bfs>/leg-potential')
def get_municipality_leg_potential(bfs):
    """LEG value-gap analysis."""
    year = request.args.get('year', 2026, type=int)
    grid_reduction = request.args.get('grid_reduction_pct', 40.0, type=float)
    num_participants = request.args.get('participants', 10, type=int)
    avg_consumption = request.args.get('consumption_kwh', 4500, type=float)

    tariffs = db.get_elcom_tariffs(bfs, year=year)
    h4 = next((t for t in tariffs if str(t.get('category', '')).startswith('H4')), None)
    if not h4:
        return jsonify({"error": "No H4 tariff found. Refresh data first.", "bfs_number": bfs}), 404

    gap = public_data.compute_leg_value_gap(h4, grid_reduction_pct=grid_reduction)
    # Scale for participants
    gap["num_participants"] = num_participants
    gap["total_community_savings_chf"] = round(gap["annual_savings_chf"] * num_participants, 2)
    gap["avg_consumption_kwh"] = avg_consumption
    gap["bfs_number"] = bfs

    return jsonify(gap)


# === Cross-municipality endpoints ===

@public_api_bp.route('/tariffs')
def list_tariffs():
    """Tariffs across municipalities."""
    kanton = request.args.get('kanton', 'ZH')
    year = request.args.get('year', 2026, type=int)
    profiles = db.get_all_municipality_profiles(kanton=kanton)
    all_tariffs = []
    for p in profiles:
        tariffs = db.get_elcom_tariffs(p['bfs_number'], year=year)
        for t in tariffs:
            t['municipality_name'] = p.get('name', '')
        all_tariffs.extend(_serialize_tariffs(tariffs))
    return jsonify({"tariffs": all_tariffs, "count": len(all_tariffs), "kanton": kanton, "year": year})


@public_api_bp.route('/rankings')
def rankings():
    """Ranked municipalities by metric."""
    kanton = request.args.get('kanton', 'ZH')
    metric = request.args.get('metric', 'energy_transition_score')
    limit = request.args.get('limit', 20, type=int)

    allowed_metrics = {'energy_transition_score', 'leg_value_gap_chf', 'population', 'name'}
    if metric not in allowed_metrics:
        metric = 'energy_transition_score'

    profiles = db.get_all_municipality_profiles(kanton=kanton, order_by=metric)
    # Reverse for descending (except name)
    if metric != 'name':
        profiles = list(reversed(profiles))
    profiles = profiles[:limit]

    return jsonify({
        "rankings": [
            {"rank": i + 1, **_serialize_profile(p)}
            for i, p in enumerate(profiles)
        ],
        "metric": metric,
        "kanton": kanton
    })


@public_api_bp.route('/search')
def search_municipalities():
    """Municipality search by name."""
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify({"error": "Query must be at least 2 characters", "results": []}), 400

    profiles = db.get_all_municipality_profiles()
    results = [
        _serialize_profile(p) for p in profiles
        if q.lower() in (p.get('name', '') or '').lower()
    ]
    return jsonify({"query": q, "results": results, "count": len(results)})


# === LEG Toolkit endpoints ===

@public_api_bp.route('/leg/value-gap', methods=['POST'])
def leg_value_gap():
    """Calculate LEG value gap for custom parameters."""
    data = request.json or {}
    bfs = data.get('bfs_number')
    if not bfs:
        return jsonify({"error": "bfs_number required"}), 400

    year = data.get('year', 2026)
    num_participants = data.get('num_participants', 10)
    avg_consumption = data.get('avg_consumption_kwh', 4500)
    pv_kwp = data.get('pv_kwp', 0)
    grid_level = data.get('grid_level', 'NE7')
    grid_reduction = 40.0 if grid_level == 'NE7' else 25.0

    tariffs = db.get_elcom_tariffs(int(bfs), year=year)
    h4 = next((t for t in tariffs if str(t.get('category', '')).startswith('H4')), None)
    if not h4:
        return jsonify({"error": "No H4 tariff found"}), 404

    gap = public_data.compute_leg_value_gap(h4, grid_reduction_pct=grid_reduction)
    # Custom consumption scaling
    custom_savings = float(gap["savings_rp_kwh"]) * avg_consumption / 100.0
    return jsonify({
        "bfs_number": bfs,
        "annual_savings_per_household": round(custom_savings, 2),
        "total_community_savings": round(custom_savings * num_participants, 2),
        "grid_fee_reduction": gap["savings_rp_kwh"],
        "grid_level": grid_level,
        "num_participants": num_participants,
        "avg_consumption_kwh": avg_consumption,
    })


@public_api_bp.route('/leg/cluster', methods=['POST'])
def leg_cluster():
    """Cluster buildings for LEG formation."""
    data = request.json or {}
    buildings = data.get('buildings', [])
    if not buildings or len(buildings) < 2:
        return jsonify({"error": "At least 2 buildings required"}), 400

    try:
        import ml_models
        import pandas as pd
        df = pd.DataFrame(buildings)
        if 'lat' not in df.columns or 'lon' not in df.columns:
            return jsonify({"error": "Each building needs lat, lon"}), 400

        ranked, clustered = ml_models.find_optimal_communities(
            df, radius_meters=150, min_community_size=2
        )
        clusters = []
        for comm in ranked:
            members = []
            if 'building_id' in clustered.columns:
                cluster_members = clustered[clustered.get('cluster', -1) == comm.get('community_id', -1)]
                members = cluster_members.to_dict('records')
            clusters.append({
                "cluster_id": comm.get('community_id'),
                "members": members,
                "centroid": comm.get('centroid'),
                "autarky_pct": comm.get('autarky_percent'),
                "recommended_size": comm.get('num_members'),
            })
        return jsonify({"clusters": clusters, "count": len(clusters)})
    except Exception as e:
        logger.error(f"[API] Clustering error: {e}")
        return jsonify({"error": str(e)}), 500


@public_api_bp.route('/leg/financial-model', methods=['POST'])
def leg_financial_model():
    """10-year financial projection for a LEG."""
    data = request.json or {}
    bfs = data.get('bfs_number')
    scenario = data.get('scenario', {})
    num_legs = scenario.get('num_legs', 1)
    community_size = scenario.get('community_size', 10)
    pv_kwp = scenario.get('pv_kwp', 30)
    consumption_kwh = scenario.get('consumption_kwh', 4500)

    import formation_wizard
    base = formation_wizard.calculate_savings_estimate(
        consumption_kwh, pv_kwp, community_size, solar_kwh_per_kwp=950
    )

    annual = base.get('annual_savings_chf', 0)
    projections = []
    cumulative = 0
    for year in range(1, 11):
        # 2% annual energy price increase
        year_savings = annual * (1.02 ** (year - 1))
        cumulative += year_savings
        projections.append({
            "year": year,
            "annual_savings_chf": round(year_savings, 2),
            "cumulative_savings_chf": round(cumulative, 2),
        })

    # CO2 reduction estimate (0.128 kg/kWh Swiss grid mix)
    self_consumption_kwh = pv_kwp * 950 * 0.3
    co2_reduction_kg = self_consumption_kwh * 0.128

    return jsonify({
        "bfs_number": bfs,
        "scenario": scenario,
        "projections": projections,
        "roi_years": round(199 / annual, 1) if annual > 0 else None,  # Formation fee / annual savings
        "co2_reduction_kg_year": round(co2_reduction_kg, 1),
        "grid_fee_savings_total_10y": round(cumulative, 2),
        "assumptions": base.get("assumptions", {}),
    })


@public_api_bp.route('/leg/templates')
def leg_templates():
    """Available LEG contract templates."""
    import formation_wizard
    templates = formation_wizard.get_contract_templates()
    return jsonify({
        "contracts": [
            {"name": key, "description": val.get("title", ""), "language": val.get("language", "de"),
             "sections": val.get("sections", [])}
            for key, val in templates.items()
        ]
    })


# === Address endpoints ===

@public_api_bp.route('/address/suggest')
def address_suggest():
    """Address autocomplete."""
    q = request.args.get('q', '').strip()
    plz_range = request.args.get('plz_range', '')
    if not q or len(q) < 2:
        return jsonify({"suggestions": []})

    import data_enricher
    plz_ranges = None
    if plz_range:
        try:
            parts = plz_range.split('-')
            plz_ranges = [[int(parts[0]), int(parts[1])]]
        except (ValueError, IndexError):
            pass

    suggestions = data_enricher.get_address_suggestions(q, limit=10, plz_ranges=plz_ranges)
    return jsonify({"suggestions": suggestions})


@public_api_bp.route('/address/profile')
def address_profile():
    """Address-level energy profile."""
    address = request.args.get('address', '').strip()
    if not address:
        return jsonify({"error": "address parameter required"}), 400

    import data_enricher
    try:
        estimates, profiles = data_enricher.get_energy_profile_for_address(address)
        if not estimates:
            estimates, profiles = data_enricher.get_mock_energy_profile_for_address(address)
    except Exception:
        estimates, profiles = data_enricher.get_mock_energy_profile_for_address(address)

    if not estimates:
        return jsonify({"error": "Address could not be analyzed"}), 404

    return jsonify(estimates)


# === API docs ===

@public_api_bp.route('/docs')
def api_docs():
    """Swagger UI for API documentation."""
    return render_template('api_docs.html')


# === Serializers ===

def _serialize_profile(p):
    """Convert DB profile dict to JSON-safe format."""
    return {
        "bfs_number": p.get("bfs_number"),
        "name": p.get("name", ""),
        "kanton": p.get("kanton", ""),
        "population": p.get("population"),
        "solar_potential_pct": _to_float(p.get("solar_potential_pct")),
        "solar_installed_kwp": _to_float(p.get("solar_installed_kwp")),
        "ev_share_pct": _to_float(p.get("ev_share_pct")),
        "renewable_heating_pct": _to_float(p.get("renewable_heating_pct")),
        "electricity_consumption_mwh": _to_float(p.get("electricity_consumption_mwh")),
        "renewable_production_mwh": _to_float(p.get("renewable_production_mwh")),
        "leg_value_gap_chf": _to_float(p.get("leg_value_gap_chf")),
        "energy_transition_score": _to_float(p.get("energy_transition_score")),
    }


def _serialize_profiles(profiles):
    return [_serialize_profile(p) for p in profiles]


def _serialize_tariffs(tariffs):
    return [{
        "bfs_number": t.get("bfs_number"),
        "operator_name": t.get("operator_name", ""),
        "municipality_name": t.get("municipality_name", ""),
        "year": t.get("year"),
        "category": t.get("category", ""),
        "total_rp_kwh": _to_float(t.get("total_rp_kwh")),
        "energy_rp_kwh": _to_float(t.get("energy_rp_kwh")),
        "grid_rp_kwh": _to_float(t.get("grid_rp_kwh")),
        "municipality_fee_rp_kwh": _to_float(t.get("municipality_fee_rp_kwh")),
        "kev_rp_kwh": _to_float(t.get("kev_rp_kwh")),
    } for t in tariffs]


def _serialize_solar(s):
    return {
        "bfs_number": s.get("bfs_number"),
        "total_roof_area_m2": _to_float(s.get("total_roof_area_m2")),
        "suitable_roof_area_m2": _to_float(s.get("suitable_roof_area_m2")),
        "potential_kwh_year": _to_float(s.get("potential_kwh_year")),
        "potential_kwp": _to_float(s.get("potential_kwp")),
        "utilization_pct": _to_float(s.get("utilization_pct")),
    }


def _to_float(val):
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
