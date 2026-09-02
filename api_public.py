# SPDX-License-Identifier: AGPL-3.0-or-later
"""
OpenLEG Public API Blueprint.
Open-source Swiss energy data API: municipalities, tariffs, solar, LEG toolkit.
No auth required. Rate limited. CORS enabled.
"""

import logging

from flask import Blueprint, g, jsonify, render_template, request

import database as db
import formation_wizard
import homepage_view_model
import municipality_profile
import public_data
import ranking as ranking_module
import registry_intake
from cantons import SWISS_CANTONS

logger = logging.getLogger(__name__)

public_api_bp = Blueprint("public_api", __name__, url_prefix="/api/v1")


# === CORS ===


@public_api_bp.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


# === Rate limiting helper ===

_request_counts: dict[str, int] = {}


def _rate_limit_key():
    return request.headers.get("X-Forwarded-For", request.remote_addr)


# === Municipality endpoints ===


@public_api_bp.route("/site/home")
def site_home():
    """Return the public-safe homepage bootstrap model for the website BFF."""
    territory = (
        g.tenant.get("territory", "zurich") if hasattr(g, "tenant") else "zurich"
    )
    model = homepage_view_model.build_homepage_view_model(territory)
    return jsonify(
        {
            "schema_version": model["schema_version"],
            "stats": model["stats"],
            "ranking": model["ranking"],
        }
    )


@public_api_bp.route("/site/rankings")
def site_rankings():
    """Return the public Solarnutzungs ranking used by the website BFF."""
    kanton, _display_kanton = _normalize_kanton_param(request.args.get("kanton"))
    size = _normalize_choice(
        request.args.get("size"), {"small", "medium", "large", "xl"}
    )
    density = _normalize_choice(
        request.args.get("density"), {"low", "mid", "high", "very_high"}
    )
    limit = _normalize_limit(
        request.args.get("limit"), default=250, minimum=1, maximum=3000
    )
    rows = ranking_module.Ranking.load().standings(
        kanton=kanton, size=size, density=density
    )
    return jsonify(
        {
            "rankings": [_serialize_site_ranking(row) for row in rows[:limit]],
            "count": len(rows),
            "limit": limit,
        }
    )


@public_api_bp.route("/site/rankings/movers")
def site_ranking_movers():
    """Return public year-over-year Solarnutzungs changes for the website BFF."""
    kanton, _display_kanton = _normalize_kanton_param(request.args.get("kanton"))
    size = _normalize_choice(
        request.args.get("size"), {"small", "medium", "large", "xl"}
    )
    density = _normalize_choice(
        request.args.get("density"), {"low", "mid", "high", "very_high"}
    )
    limit = _normalize_limit(
        request.args.get("limit"), default=100, minimum=1, maximum=3000
    )
    rows = ranking_module.Ranking([]).movers(kanton=kanton, size=size, density=density)
    return jsonify(
        {
            "movers": [_serialize_site_mover(row) for row in rows[:limit]],
            "count": len(rows),
            "limit": limit,
        }
    )


@public_api_bp.route("/registry")
def registry_entries():
    """List published LEG registry entries without contact or moderation data."""
    kanton, _display_kanton = _normalize_kanton_param(request.args.get("kanton"))
    plz = (request.args.get("plz") or "").strip() or None
    q = (request.args.get("q") or "").strip()[:100] or None
    leg_status = (request.args.get("leg_status") or "").strip().lower()
    if leg_status not in {"planung", "gruendung", "aktiv", "pausiert"}:
        leg_status = None
    limit = _normalize_limit(
        request.args.get("limit"), default=100, minimum=1, maximum=500
    )
    entries = db.list_registry_entries(
        kanton=kanton,
        plz=plz,
        leg_status=leg_status,
        q=q,
        moderation_status="published",
        limit=limit,
    )
    serialized = [_serialize_registry_entry(entry) for entry in entries]
    return jsonify({"entries": serialized, "count": len(serialized)})


@public_api_bp.route("/registry/<slug>")
def registry_entry(slug):
    """Return one published LEG registry entry with public fields only."""
    entry = db.get_registry_entry_by_slug(slug)
    if not entry or entry.get("moderation_status") != "published":
        return jsonify({"error": "Registry entry not found"}), 404
    return jsonify(_serialize_registry_entry(entry))


@public_api_bp.route("/municipalities")
def list_municipalities():
    """List all municipalities with profiles."""
    kanton_filter, kanton = _normalize_kanton_param(request.args.get("kanton"))
    search = (request.args.get("search") or "").strip().lower()
    order_by = request.args.get("order_by", "name")
    profiles = db.get_all_municipality_profiles(kanton=kanton_filter, order_by=order_by)
    if search:
        profiles = [
            profile
            for profile in profiles
            if search in (profile.get("name") or "").lower()
        ]
    return jsonify(
        {
            "municipalities": _serialize_profiles(profiles),
            "count": len(profiles),
            "kanton": kanton,
        }
    )


@public_api_bp.route("/municipalities/<int:bfs>")
def get_municipality(bfs):
    """Single municipality profile."""
    profile = db.get_municipality_profile(bfs)
    if not profile:
        return jsonify({"error": "Municipality not found", "bfs_number": bfs}), 404
    return jsonify(_serialize_profile(profile))


@public_api_bp.route("/municipalities/<int:bfs>/tariffs")
def get_municipality_tariffs(bfs):
    """ElCom tariffs for a municipality."""
    year = request.args.get("year", type=int)
    tariffs = db.get_elcom_tariffs(bfs, year=year)
    return jsonify(
        {
            "bfs_number": bfs,
            "tariffs": _serialize_tariffs(tariffs),
            "count": len(tariffs),
        }
    )


@public_api_bp.route("/municipalities/<int:bfs>/solar")
def get_municipality_solar(bfs):
    """Sonnendach data for a municipality."""
    solar = db.get_sonnendach_municipal(bfs)
    if not solar:
        return jsonify({"error": "No solar data found", "bfs_number": bfs}), 404
    return jsonify(_serialize_solar(solar))


@public_api_bp.route("/municipalities/<int:bfs>/score")
def get_municipality_score(bfs):
    """Energy transition score breakdown."""
    profile = db.get_municipality_profile(bfs)
    if not profile:
        return jsonify({"error": "Municipality not found"}), 404

    score = public_data.compute_energy_transition_score(profile)
    solar = min(float(profile.get("solar_potential_pct", 0) or 0), 100) / 100.0
    ev = min(float(profile.get("ev_share_pct", 0) or 0), 30) / 30.0
    heating = min(float(profile.get("renewable_heating_pct", 0) or 0), 100) / 100.0
    consumption = float(profile.get("electricity_consumption_mwh", 0) or 0)
    production = float(profile.get("renewable_production_mwh", 0) or 0)
    prod_ratio = min(production / consumption, 1.0) if consumption > 0 else 0

    return jsonify(
        {
            "bfs_number": bfs,
            "name": profile.get("name", ""),
            "total_score": score,
            "breakdown": {
                "solar": {
                    "weight": 30,
                    "raw_pct": float(profile.get("solar_potential_pct", 0) or 0),
                    "score": round(solar * 30, 1),
                },
                "ev": {
                    "weight": 20,
                    "raw_pct": float(profile.get("ev_share_pct", 0) or 0),
                    "score": round(ev * 20, 1),
                },
                "heating": {
                    "weight": 25,
                    "raw_pct": float(profile.get("renewable_heating_pct", 0) or 0),
                    "score": round(heating * 25, 1),
                },
                "production": {
                    "weight": 25,
                    "raw_pct": round(prod_ratio * 100, 1),
                    "score": round(prod_ratio * 25, 1),
                },
            },
        }
    )


@public_api_bp.route("/municipalities/<int:bfs>/leg-potential")
def get_municipality_leg_potential(bfs):
    """LEG value-gap analysis."""
    year = request.args.get("year", 2026, type=int)
    grid_reduction = request.args.get("grid_reduction_pct", 40.0, type=float)
    num_participants = request.args.get("participants", 10, type=int)
    avg_consumption = request.args.get("consumption_kwh", 4500, type=float)

    gap = municipality_profile.value_gap(
        bfs, year=year, grid_reduction_pct=grid_reduction
    )
    if not gap:
        return jsonify(
            {"error": "No H4 tariff found. Refresh data first.", "bfs_number": bfs}
        ), 404

    # Scale for participants
    gap["num_participants"] = num_participants
    gap["total_community_savings_chf"] = round(
        gap["annual_savings_chf"] * num_participants, 2
    )
    gap["avg_consumption_kwh"] = avg_consumption
    gap["bfs_number"] = bfs

    return jsonify(gap)


# === Cross-municipality endpoints ===


@public_api_bp.route("/tariffs")
def list_tariffs():
    """Tariffs across municipalities."""
    kanton_filter, kanton = _normalize_kanton_param(request.args.get("kanton"))
    year = request.args.get("year", 2026, type=int)
    all_tariffs = _serialize_tariffs(
        db.get_all_elcom_tariffs(year=year, kanton=kanton_filter)
    )
    return jsonify(
        {
            "tariffs": all_tariffs,
            "count": len(all_tariffs),
            "kanton": kanton,
            "year": year,
        }
    )


@public_api_bp.route("/rankings")
def rankings():
    """Ranked municipalities by metric."""
    kanton_filter, kanton = _normalize_kanton_param(request.args.get("kanton"))
    metric = request.args.get("metric", "energy_transition_score")
    limit = request.args.get("limit", 20, type=int)

    allowed_metrics = {
        "energy_transition_score",
        "leg_value_gap_chf",
        "population",
        "name",
    }
    if metric not in allowed_metrics:
        metric = "energy_transition_score"

    profiles = db.get_all_municipality_profiles(kanton=kanton_filter, order_by=metric)
    # Reverse for descending (except name)
    if metric != "name":
        profiles = list(reversed(profiles))
    profiles = profiles[:limit]

    return jsonify(
        {
            "rankings": [
                {"rank": i + 1, **_serialize_profile(p)} for i, p in enumerate(profiles)
            ],
            "metric": metric,
            "kanton": kanton,
        }
    )


@public_api_bp.route("/search")
def search_municipalities():
    """Municipality search by name."""
    q = request.args.get("q", "").strip()
    if not q or len(q) < 2:
        return jsonify(
            {"error": "Query must be at least 2 characters", "results": []}
        ), 400
    limit = _normalize_limit(
        request.args.get("limit"), default=10, minimum=1, maximum=50
    )

    profiles = db.get_all_municipality_profiles()
    results = [
        _serialize_profile(p)
        for p in profiles
        if q.lower() in (p.get("name", "") or "").lower()
    ][:limit]
    return jsonify(
        {"query": q, "results": results, "count": len(results), "limit": limit}
    )


# === LEG Toolkit endpoints ===


@public_api_bp.route("/leg/value-gap", methods=["POST"])
def leg_value_gap():
    """Calculate LEG value gap for custom parameters."""
    data = request.json or {}
    bfs = data.get("bfs_number")
    if not bfs:
        return jsonify({"error": "bfs_number required"}), 400

    year = data.get("year", 2026)
    num_participants = data.get("num_participants", 10)
    avg_consumption = data.get("avg_consumption_kwh", 4500)
    grid_level = data.get("grid_level", "NE7")
    grid_reduction = 40.0 if grid_level == "NE7" else 25.0

    gap = municipality_profile.value_gap(
        int(bfs), year=year, grid_reduction_pct=grid_reduction
    )
    if not gap:
        return jsonify({"error": "No H4 tariff found"}), 404

    # Custom consumption scaling
    custom_savings = float(gap["savings_rp_kwh"]) * avg_consumption / 100.0
    return jsonify(
        {
            "bfs_number": bfs,
            "annual_savings_per_household": round(custom_savings, 2),
            "total_community_savings": round(custom_savings * num_participants, 2),
            "grid_fee_reduction": gap["savings_rp_kwh"],
            "grid_level": grid_level,
            "num_participants": num_participants,
            "avg_consumption_kwh": avg_consumption,
        }
    )


@public_api_bp.route("/leg/cluster", methods=["POST"])
def leg_cluster():
    """Cluster buildings for LEG formation."""
    data = request.json or {}
    buildings = data.get("buildings", [])
    if not buildings or len(buildings) < 2:
        return jsonify({"error": "At least 2 buildings required"}), 400

    try:
        import pandas as pd

        import ml_models

        df = pd.DataFrame(buildings)
        if "lat" not in df.columns or "lon" not in df.columns:
            return jsonify({"error": "Each building needs lat, lon"}), 400

        ranked, clustered = ml_models.find_optimal_communities(
            df, radius_meters=150, min_community_size=2
        )
        clusters = []
        for comm in ranked:
            members = []
            if "building_id" in clustered.columns:
                cluster_members = clustered[
                    clustered.get("cluster", -1) == comm.get("community_id", -1)
                ]
                members = cluster_members.to_dict("records")
            clusters.append(
                {
                    "cluster_id": comm.get("community_id"),
                    "members": members,
                    "centroid": comm.get("centroid"),
                    "autarky_pct": comm.get("autarky_percent"),
                    "recommended_size": comm.get("num_members"),
                }
            )
        return jsonify({"clusters": clusters, "count": len(clusters)})
    except Exception:
        logger.exception("[API] Clustering error")
        return jsonify({"error": "Clustering fehlgeschlagen."}), 500


@public_api_bp.route("/leg/financial-model", methods=["POST"])
def leg_financial_model():
    """10-year financial projection for a LEG."""
    data = request.json or {}
    bfs = data.get("bfs_number")
    scenario = data.get("scenario", {})
    community_size = scenario.get("community_size", 10)
    pv_kwp = scenario.get("pv_kwp", 30)
    consumption_kwh = scenario.get("consumption_kwh", 4500)

    base = formation_wizard.calculate_savings_estimate(
        consumption_kwh,
        pv_kwp,
        community_size,
        solar_kwh_per_kwp=formation_wizard.DEFAULT_SOLAR_KWH_PER_KWP,
    )

    annual = base.get("annual_savings_chf", 0)
    projections = []
    cumulative = 0
    for year in range(1, 11):
        # 2% annual energy price increase
        year_savings = annual * (1.02 ** (year - 1))
        cumulative += year_savings
        projections.append(
            {
                "year": year,
                "annual_savings_chf": round(year_savings, 2),
                "cumulative_savings_chf": round(cumulative, 2),
            }
        )

    # CO2 reduction estimate (0.128 kg/kWh Swiss grid mix)
    self_consumption_kwh = pv_kwp * formation_wizard.DEFAULT_SOLAR_KWH_PER_KWP * 0.3
    co2_reduction_kg = self_consumption_kwh * 0.128

    return jsonify(
        {
            "bfs_number": bfs,
            "scenario": scenario,
            "projections": projections,
            "roi_years": round(199 / annual, 1)
            if annual > 0
            else None,  # Formation fee / annual savings
            "co2_reduction_kg_year": round(co2_reduction_kg, 1),
            "grid_fee_savings_total_10y": round(cumulative, 2),
            "assumptions": base.get("assumptions", {}),
        }
    )


@public_api_bp.route("/leg/templates")
def leg_templates():
    """Available LEG contract templates."""
    import formation_wizard

    templates = formation_wizard.get_contract_templates()
    return jsonify(
        {
            "contracts": [
                {
                    "name": key,
                    "description": val.get("title", ""),
                    "language": val.get("language", "de"),
                    "sections": val.get("sections", []),
                }
                for key, val in templates.items()
            ]
        }
    )


# === Address endpoints ===


@public_api_bp.route("/address/suggest")
def address_suggest():
    """Address autocomplete."""
    q = request.args.get("q", "").strip()
    plz_range = request.args.get("plz_range", "")

    import data_enricher

    plz_ranges = None
    if plz_range:
        try:
            parts = plz_range.split("-")
            plz_ranges = [[int(parts[0]), int(parts[1])]]
        except (ValueError, IndexError):
            pass

    payload, status = data_enricher.resolve_address_suggestions(
        q, limit=10, plz_ranges=plz_ranges
    )
    return jsonify(payload), status


@public_api_bp.route("/address/profile")
def address_profile():
    """Address-level energy profile."""
    address = request.args.get("address", "").strip()
    if not address:
        return jsonify({"error": "address parameter required"}), 400

    import data_enricher

    try:
        estimates, _profiles = data_enricher.get_energy_profile_for_address(address)
        if not estimates:
            estimates, _profiles = data_enricher.get_mock_energy_profile_for_address(
                address
            )
    except Exception:
        estimates, _profiles = data_enricher.get_mock_energy_profile_for_address(
            address
        )

    if not estimates:
        return jsonify({"error": "Address could not be analyzed"}), 404

    return jsonify(estimates)


# === API docs ===


@public_api_bp.route("/docs")
def api_docs():
    """Swagger UI for API documentation."""
    return render_template(
        "api_docs.html",
        site_url=request.url_root.rstrip("/"),
        canonical_path="/api/v1/docs",
    )


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


def _serialize_site_ranking(row):
    """Whitelist Solarnutzungs ranking fields for the public website."""
    fields = (
        "rank",
        "bfs_number",
        "name",
        "kanton",
        "population",
        "pv_score_pct",
        "display_score",
        "score_over_100",
        "pv_untapped_kw",
    )
    return {field: row.get(field) for field in fields}


def _serialize_site_mover(row):
    """Whitelist Solarnutzungs change fields for the public website."""
    fields = (
        "bfs_number",
        "name",
        "kanton",
        "year",
        "score_now",
        "score_prev",
        "delta",
    )
    return {field: row.get(field) for field in fields}


def _serialize_registry_entry(entry):
    """Whitelist fields intended for the public LEG directory."""
    return {
        "slug": entry.get("slug", ""),
        "name": entry.get("name", ""),
        "kanton": entry.get("kanton", ""),
        "plz": entry.get("plz", ""),
        "ort": entry.get("ort", ""),
        "vnb_name": entry.get("vnb_name", ""),
        "leg_status": entry.get("leg_status", ""),
        "member_count_estimate": entry.get("member_count_estimate"),
        "description": entry.get("description", ""),
        "website_url": registry_intake.normalize_website_url(
            entry.get("website_url", "")
        ),
    }


def _serialize_tariffs(tariffs):
    return [
        {
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
        }
        for t in tariffs
    ]


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


def _normalize_kanton_param(raw_value):
    raw = (raw_value or "all").strip().upper()
    if raw in ("", "ALL"):
        return None, "all"
    if raw in SWISS_CANTONS:
        return raw, raw
    return None, "all"


def _normalize_choice(raw_value, allowed):
    value = (raw_value or "").strip().lower()
    return value if value in allowed else None


def _normalize_limit(raw_value, default=10, minimum=1, maximum=50):
    try:
        limit = int(raw_value)
    except (TypeError, ValueError):
        return default
    if limit < minimum:
        return minimum
    if limit > maximum:
        return maximum
    return limit
