"""
OpenLEG Insights Engine.
Aggregates anonymized smart meter data into intelligence products for B2B API.
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import database as db

logger = logging.getLogger(__name__)


def compute_load_profiles(plz: str = None, period: str = 'month') -> Dict:
    """Compute average load profiles by PLZ, building type, time-of-day."""
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                query = """
                    SELECT
                        b.plz,
                        b.building_type,
                        EXTRACT(HOUR FROM mr.timestamp) as hour,
                        EXTRACT(DOW FROM mr.timestamp) as day_of_week,
                        AVG(mr.consumption_kwh) as avg_consumption,
                        AVG(mr.production_kwh) as avg_production,
                        COUNT(DISTINCT mr.building_id) as sample_size
                    FROM meter_readings mr
                    JOIN buildings b ON mr.building_id = b.building_id
                    JOIN data_consents dc ON mr.building_id = dc.building_id
                    WHERE dc.tier >= 2 AND dc.revoked_at IS NULL
                """
                params = []
                if plz:
                    query += " AND b.plz = %s"
                    params.append(plz)

                query += """
                    GROUP BY b.plz, b.building_type, hour, day_of_week
                    ORDER BY b.plz, hour
                """
                cur.execute(query, params)
                rows = [dict(r) for r in cur.fetchall()]

                # Structure result
                profiles = {}
                for row in rows:
                    key = f"{row['plz']}_{row['building_type']}"
                    if key not in profiles:
                        profiles[key] = {"plz": row['plz'], "building_type": row['building_type'], "hourly": {}}
                    profiles[key]["hourly"][f"{int(row['hour'])}h"] = {
                        "avg_consumption_kwh": float(row['avg_consumption'] or 0),
                        "avg_production_kwh": float(row['avg_production'] or 0),
                        "sample_size": row['sample_size']
                    }

                return {"profiles": list(profiles.values()), "period": period, "computed_at": datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"[INSIGHTS] Error computing load profiles: {e}")
        return {"profiles": [], "error": str(e)}


def compute_solar_index(kanton: str = 'ZH') -> Dict:
    """PV penetration and battery storage rates by municipality."""
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        b.plz,
                        COUNT(*) as total_buildings,
                        COUNT(CASE WHEN b.potential_pv_kwp > 0 THEN 1 END) as with_pv_potential,
                        AVG(b.potential_pv_kwp) as avg_pv_kwp,
                        COUNT(CASE WHEN mr.production_kwh > 0 THEN 1 END) as active_producers
                    FROM buildings b
                    LEFT JOIN (
                        SELECT DISTINCT building_id,
                               CASE WHEN SUM(production_kwh) > 0 THEN 1 ELSE 0 END as production_kwh
                        FROM meter_readings
                        GROUP BY building_id
                    ) mr ON b.building_id = mr.building_id
                    JOIN data_consents dc ON b.building_id = dc.building_id
                    WHERE dc.tier >= 2 AND dc.revoked_at IS NULL
                    GROUP BY b.plz
                    ORDER BY b.plz
                """)
                rows = [dict(r) for r in cur.fetchall()]

                for row in rows:
                    total = row['total_buildings'] or 1
                    row['pv_penetration_pct'] = round((row['active_producers'] or 0) / total * 100, 1)
                    row['avg_pv_kwp'] = round(float(row['avg_pv_kwp'] or 0), 1)

                return {"solar_index": rows, "kanton": kanton, "computed_at": datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"[INSIGHTS] Error computing solar index: {e}")
        return {"solar_index": [], "error": str(e)}


def compute_flexibility_potential() -> Dict:
    """Estimate demand response potential from load profile variability."""
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        b.plz,
                        COUNT(DISTINCT mr.building_id) as households,
                        AVG(mr.consumption_kwh) as avg_load_kwh,
                        STDDEV(mr.consumption_kwh) as load_variability,
                        MAX(mr.consumption_kwh) as peak_load_kwh
                    FROM meter_readings mr
                    JOIN buildings b ON mr.building_id = b.building_id
                    JOIN data_consents dc ON mr.building_id = dc.building_id
                    WHERE dc.tier >= 3 AND dc.revoked_at IS NULL
                    GROUP BY b.plz
                """)
                rows = [dict(r) for r in cur.fetchall()]

                for row in rows:
                    avg = float(row['avg_load_kwh'] or 0)
                    peak = float(row['peak_load_kwh'] or 0)
                    row['flexibility_potential_kwh'] = round((peak - avg) * float(row['households'] or 0), 1)
                    row['load_variability'] = round(float(row['load_variability'] or 0), 4)

                return {"flexibility": rows, "computed_at": datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"[INSIGHTS] Error computing flexibility: {e}")
        return {"flexibility": [], "error": str(e)}


def compute_community_signals() -> Dict:
    """Early indicators of organizing neighborhoods for LEG formation."""
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        b.plz,
                        COUNT(*) as registered_count,
                        COUNT(CASE WHEN cm.status = 'confirmed' THEN 1 END) as in_community,
                        COUNT(CASE WHEN b.created_at > CURRENT_TIMESTAMP - INTERVAL '30 days' THEN 1 END) as recent_signups
                    FROM buildings b
                    LEFT JOIN community_members cm ON b.building_id = cm.building_id
                    WHERE b.verified = TRUE
                    GROUP BY b.plz
                    HAVING COUNT(*) >= 3
                    ORDER BY registered_count DESC
                """)
                rows = [dict(r) for r in cur.fetchall()]

                for row in rows:
                    total = row['registered_count'] or 1
                    row['community_rate_pct'] = round((row['in_community'] or 0) / total * 100, 1)
                    row['momentum_score'] = min(100, (row['recent_signups'] or 0) * 20)

                return {"signals": rows, "computed_at": datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"[INSIGHTS] Error computing community signals: {e}")
        return {"signals": [], "error": str(e)}


def refresh_all_insights():
    """Recompute and cache all insight types."""
    results = {}

    for name, fn in [
        ('load_profiles', lambda: compute_load_profiles()),
        ('solar_index', lambda: compute_solar_index()),
        ('flexibility', lambda: compute_flexibility_potential()),
        ('community_signals', lambda: compute_community_signals()),
    ]:
        data = fn()
        db.save_insight(name, scope='ZH', period='current', data=data, ttl_hours=24)
        results[name] = 'ok' if 'error' not in data else data['error']

    logger.info(f"[INSIGHTS] Refreshed all insights: {results}")
    return results


def compute_formation_pipeline(kanton: str = None) -> Dict:
    """Aggregate LEG formation pipeline by stage.

    Returns: {stages: {stage: count}, total_communities: int, avg_formation_days: float}
    """
    try:
        communities = db.list_communities_by_kanton(kanton) if kanton else db.list_all_communities()
    except Exception:
        communities = []

    stage_counts = {
        "interested": 0, "formation_started": 0, "dso_submitted": 0,
        "dso_approved": 0, "active": 0
    }
    for c in communities:
        status = c.get("status", "interested")
        if status in stage_counts:
            stage_counts[status] += 1

    return {
        "stages": stage_counts,
        "total_communities": len(communities),
        "avg_formation_days": 0,  # TODO: compute from timestamps
        "kanton": kanton or "all",
    }


def compute_grid_optimization(kanton: str = "ZH") -> Dict:
    """Compute grid optimization recommendations from LEG data.

    Returns: peak reduction potential, self-consumption ratio, recommendations
    """
    try:
        profiles = db.get_all_municipality_profiles()
        profiles = [p for p in profiles if p.get("kanton") == kanton] if kanton else profiles
    except Exception:
        profiles = []

    total_consumption = sum(p.get("electricity_consumption_mwh", 0) for p in profiles)
    total_production = sum(p.get("renewable_production_mwh", 0) for p in profiles)
    self_supply = (total_production / total_consumption * 100) if total_consumption > 0 else 0

    actions = []
    if self_supply < 20:
        actions.append("Increase PV installations in low-penetration municipalities")
    if self_supply > 30:
        actions.append("Deploy battery storage to reduce grid feed-in peaks")
    actions.append("Optimize LEG boundaries to maximize self-consumption")

    return {
        "kanton": kanton,
        "total_consumption_mwh": round(total_consumption, 0),
        "total_production_mwh": round(total_production, 0),
        "self_supply_ratio_pct": round(self_supply, 1),
        "peak_reduction_potential_pct": round(min(self_supply * 0.4, 25), 1),
        "recommended_actions": actions,
    }


def compute_community_benchmarks(kanton: str = "ZH") -> Dict:
    """Aggregate community performance benchmarks.

    Returns: avg members, avg self-supply ratio, top communities
    """
    try:
        communities = db.list_communities_by_kanton(kanton) if kanton else db.list_all_communities()
    except Exception:
        communities = []

    if not communities:
        return {
            "kanton": kanton,
            "avg_members": 0,
            "avg_self_supply_ratio": 0,
            "total_communities": 0,
            "top_communities": [],
        }

    member_counts = [c.get("member_count", 0) for c in communities]
    avg_members = sum(member_counts) / len(member_counts) if member_counts else 0

    return {
        "kanton": kanton,
        "avg_members": round(avg_members, 1),
        "avg_self_supply_ratio": 0,  # TODO: compute from billing data
        "total_communities": len(communities),
        "top_communities": sorted(communities, key=lambda c: c.get("member_count", 0), reverse=True)[:5],
    }
