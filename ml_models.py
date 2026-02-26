import hashlib
import json
from math import radians, sin, cos, sqrt, atan2

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN

SIM_VERSION = "v2"
SIMULATION_START = "2025-01-01 00:00:00"
SIMULATION_INTERVAL_MINUTES = 15
SIMULATION_NUM_INTERVALS = 35040
MOCK_PROFILE_CONFIDENCE = 0.45


def _to_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_building_archetype(building_type):
    """Map raw building type into simulation archetypes."""
    if building_type is None:
        return "default"
    value = str(building_type).strip().lower()
    if value in {"efh", "single_family_home", "single-family-home", "single family home", "sfh"}:
        return "EFH"
    if value in {"mfh", "apartment", "apartment_building", "multi_family", "multi-family", "mfh_wohnhaus"}:
        return "MFH"
    if "office" in value or "buero" in value or "büro" in value:
        return "office"
    if "small_business" in value or "business" in value or "commercial" in value or "gewerbe" in value:
        return "small_business"
    return "default"


def calculate_distance(lat1, lon1, lat2, lon2):
    """Calculate the great-circle distance between two points (in meters)."""
    radius_m = 6371e3
    phi1 = radians(lat1)
    phi2 = radians(lat2)
    delta_phi = radians(lat2 - lat1)
    delta_lambda = radians(lon2 - lon1)
    a = (
        sin(delta_phi / 2) * sin(delta_phi / 2)
        + cos(phi1) * cos(phi2) * sin(delta_lambda / 2) * sin(delta_lambda / 2)
    )
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return radius_m * c


def build_community_signature(community_df, city_id=None, sim_version=SIM_VERSION):
    """Deterministic hash for a community independent from DataFrame row order."""
    rows = []
    if community_df is not None and not community_df.empty:
        for _, row in community_df.iterrows():
            rows.append({
                "building_id": str(row.get("building_id", "")),
                "annual_consumption_kwh": round(_to_float(row.get("annual_consumption_kwh")), 3),
                "potential_pv_kwp": round(_to_float(row.get("potential_pv_kwp")), 3),
                "archetype": normalize_building_archetype(row.get("building_type")),
            })
    rows = sorted(rows, key=lambda item: item["building_id"])
    payload = {
        "city_id": city_id or "",
        "sim_version": sim_version,
        "interval_minutes": SIMULATION_INTERVAL_MINUTES,
        "num_intervals": SIMULATION_NUM_INTERVALS,
        "rows": rows,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_mock_profiles(
    annual_consumption_kwh,
    potential_pv_kwp,
    num_intervals=SIMULATION_NUM_INTERVALS,
    archetype="default",
    start=SIMULATION_START,
):
    """Generate simplified 15-minute profiles by building archetype."""
    timestamps = pd.date_range(start=start, periods=num_intervals, freq=f"{SIMULATION_INTERVAL_MINUTES}min")
    time_of_day = np.array(timestamps.hour) + np.array(timestamps.minute) / 60.0
    day_of_week = np.array(timestamps.dayofweek)
    archetype = normalize_building_archetype(archetype)

    # Consumption shape
    if archetype == "EFH":
        morning_peak = np.exp(-0.5 * ((time_of_day - 7.5) / 2.0) ** 2)
        evening_peak = np.exp(-0.5 * ((time_of_day - 19.5) / 2.8) ** 2)
        base = 0.18 + 1.8 * morning_peak + 2.6 * evening_peak
        weekend_boost = np.where(day_of_week >= 5, 1.12, 1.0)
        consumption_shape = base * weekend_boost
    elif archetype == "MFH":
        morning_peak = np.exp(-0.5 * ((time_of_day - 7.0) / 2.5) ** 2)
        evening_peak = np.exp(-0.5 * ((time_of_day - 20.0) / 3.0) ** 2)
        base = 0.28 + 1.4 * morning_peak + 1.8 * evening_peak
        weekend_boost = np.where(day_of_week >= 5, 1.06, 1.0)
        consumption_shape = base * weekend_boost
    elif archetype == "office":
        work_hours = ((time_of_day >= 8) & (time_of_day <= 18)).astype(float)
        weekday = (day_of_week < 5).astype(float)
        base = 0.12 + (2.3 * work_hours * weekday)
        lunch_peak = np.exp(-0.5 * ((time_of_day - 12.0) / 2.0) ** 2) * weekday
        consumption_shape = base + 0.6 * lunch_peak
    elif archetype == "small_business":
        work_hours = ((time_of_day >= 7) & (time_of_day <= 19)).astype(float)
        weekday = (day_of_week < 6).astype(float)
        base = 0.15 + (1.8 * work_hours * weekday)
        shoulder = np.exp(-0.5 * ((time_of_day - 10.5) / 2.5) ** 2)
        consumption_shape = base + 0.5 * shoulder
    else:
        consumption_shape = np.cos((time_of_day - 13.5) * (np.pi / 12)) ** 2
        consumption_shape[(time_of_day < 6) | (time_of_day > 22)] *= 0.5

    consumption_shape = np.array(consumption_shape)
    if consumption_shape.sum() > 0:
        normalized_consumption = consumption_shape / consumption_shape.sum()
    else:
        normalized_consumption = np.zeros_like(consumption_shape)
    annual_consumption_kwh = max(_to_float(annual_consumption_kwh), 0.0)
    consumption_profile_kw = normalized_consumption * (annual_consumption_kwh / 0.25)

    # PV shape
    day_of_year = np.array(timestamps.dayofyear)
    seasonal_factor = 1 + 0.5 * np.cos((day_of_year - 172) * (2 * np.pi / 365))
    pv_time_factor = np.maximum(0, (time_of_day - 6) * (np.pi / 12))
    pv_sin = np.sin(pv_time_factor)
    pv_shape = np.power(np.where(pv_sin > 0, pv_sin, 0), 1.5) * seasonal_factor

    # Slight weekday bias for office and small businesses.
    if archetype in {"office", "small_business"}:
        weekday = (day_of_week < 5).astype(float)
        pv_shape = pv_shape * (0.85 + 0.15 * weekday)

    if pv_shape.max() > 0:
        normalized_pv = pv_shape / pv_shape.max()
    else:
        normalized_pv = np.zeros_like(pv_shape)
    potential_pv_kwp = max(_to_float(potential_pv_kwp), 0.0)
    pv_profile_kw = normalized_pv * potential_pv_kwp

    return pd.DataFrame({
        "consumption_kw": consumption_profile_kw,
        "production_kw": pv_profile_kw,
    }, index=timestamps)


def _cache_get(cache_backend, key):
    if not cache_backend or not key:
        return None
    try:
        if isinstance(cache_backend, dict):
            getter = cache_backend.get("get")
        else:
            getter = getattr(cache_backend, "get", None)
        if not callable(getter):
            return None
        return getter(key)
    except Exception:
        return None


def _cache_set(cache_backend, key, value, ttl_seconds=86400):
    if not cache_backend or not key:
        return False
    try:
        if isinstance(cache_backend, dict):
            setter = cache_backend.get("set")
        else:
            setter = getattr(cache_backend, "set", None)
        if not callable(setter):
            return False
        return bool(setter(key, value, ttl_seconds))
    except Exception:
        return False


def _window_timestamps(window=None):
    window = window or {}
    start = window.get("start", SIMULATION_START)
    num_intervals = int(window.get("num_intervals", SIMULATION_NUM_INTERVALS))
    return pd.date_range(start=start, periods=num_intervals, freq=f"{SIMULATION_INTERVAL_MINUTES}min")


def _load_real_meter_profile(building_id, timestamps):
    import database as db
    import meter_data

    rows = db.get_meter_profile_15min(building_id, start=timestamps[0], end=timestamps[-1])
    if not rows:
        return None, {
            "usable_for_simulation": False,
            "quality_score": 0.0,
            "coverage_ratio": 0.0,
        }

    tuples = [
        (
            row.get("timestamp"),
            _to_float(row.get("consumption_kwh")),
            _to_float(row.get("production_kwh")),
            _to_float(row.get("feed_in_kwh")),
        )
        for row in rows
        if row.get("timestamp") is not None
    ]
    quality = meter_data.score_meter_profile_usability(
        tuples,
        expected_interval_minutes=SIMULATION_INTERVAL_MINUTES,
        expected_points=len(timestamps),
    )
    if not quality.get("usable_for_simulation"):
        return None, quality

    profile_df = pd.DataFrame(rows)
    if profile_df.empty or "timestamp" not in profile_df.columns:
        return None, quality
    profile_df["timestamp"] = pd.to_datetime(profile_df["timestamp"], errors="coerce")
    profile_df = profile_df.dropna(subset=["timestamp"])
    if profile_df.empty:
        return None, quality
    profile_df = profile_df.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")
    profile_df = profile_df.set_index("timestamp")

    consumption_kwh = pd.to_numeric(profile_df.get("consumption_kwh"), errors="coerce").fillna(0.0)
    production_kwh = pd.to_numeric(profile_df.get("production_kwh"), errors="coerce").fillna(0.0)

    aligned = pd.DataFrame(index=timestamps)
    aligned["consumption_kw"] = (consumption_kwh.reindex(timestamps).fillna(0.0) / 0.25).values
    aligned["production_kw"] = (production_kwh.reindex(timestamps).fillna(0.0) / 0.25).values
    return aligned, quality


def get_building_profile(building_row, window=None, strategy="hybrid"):
    """Resolve profile for a building (real meter first, then archetype mock)."""
    if hasattr(building_row, "to_dict"):
        row = building_row.to_dict()
    else:
        row = dict(building_row)

    timestamps = _window_timestamps(window=window)
    building_id = row.get("building_id")

    if strategy in {"hybrid", "real_first"} and building_id:
        real_profile, quality = _load_real_meter_profile(building_id, timestamps)
        if real_profile is not None:
            return real_profile, {
                "source": "real",
                "quality_score": _to_float(quality.get("quality_score"), 1.0),
                "coverage_ratio": _to_float(quality.get("coverage_ratio"), 1.0),
                "quality": quality,
            }

    archetype = normalize_building_archetype(row.get("building_type"))
    mock_profile = generate_mock_profiles(
        annual_consumption_kwh=row.get("annual_consumption_kwh"),
        potential_pv_kwp=row.get("potential_pv_kwp"),
        num_intervals=len(timestamps),
        archetype=archetype,
        start=str(timestamps[0]),
    )
    return mock_profile, {
        "source": "mock",
        "quality_score": MOCK_PROFILE_CONFIDENCE,
        "coverage_ratio": 0.0,
        "quality": {
            "usable_for_simulation": False,
            "coverage_ratio": 0.0,
            "quality_score": MOCK_PROFILE_CONFIDENCE,
        },
        "archetype": archetype,
    }


def calculate_community_autarky_details(
    community_buildings_df,
    all_profiles=None,
    profile_provider=None,
    cache_backend=None,
    cache_key=None,
    cache_ttl_seconds=86400,
    strategy="hybrid",
    window=None,
):
    """Compute autarky + metadata for a community."""
    if community_buildings_df is None or community_buildings_df.empty:
        return {
            "autarky_score": 0.0,
            "total_consumption_kwh": 0.0,
            "total_production_kwh": 0.0,
            "confidence_percent": 0.0,
            "profile_data_mix": "mock",
            "cache_hit": False,
            "source_counts": {"real": 0, "mock": 0, "provided": 0},
        }

    cached = _cache_get(cache_backend, cache_key)
    if isinstance(cached, dict):
        cached_result = cached.get("result_json", cached)
        if isinstance(cached_result, dict):
            result = dict(cached_result)
            result["cache_hit"] = True
            return result

    profile_provider = profile_provider or get_building_profile
    timestamps = _window_timestamps(window=window)
    num_points = len(timestamps)
    community_consumption = np.zeros(num_points, dtype=float)
    community_production = np.zeros(num_points, dtype=float)

    source_counts = {"real": 0, "mock": 0, "provided": 0}
    confidence_values = []

    for _, row in community_buildings_df.iterrows():
        building_id = row.get("building_id")
        profile = None
        profile_meta = None

        if isinstance(all_profiles, dict) and building_id in all_profiles:
            profile = all_profiles.get(building_id)
            profile_meta = {
                "source": "provided",
                "quality_score": 0.6,
                "coverage_ratio": 0.6,
            }

        if profile is None:
            profile, profile_meta = profile_provider(row, window=window, strategy=strategy)

        if profile is None:
            continue
        if not isinstance(profile, pd.DataFrame):
            profile = pd.DataFrame(profile)
        if "consumption_kw" not in profile.columns or "production_kw" not in profile.columns:
            continue

        consumption = pd.to_numeric(profile["consumption_kw"], errors="coerce").fillna(0.0).to_numpy()
        production = pd.to_numeric(profile["production_kw"], errors="coerce").fillna(0.0).to_numpy()

        if len(consumption) != num_points:
            if len(consumption) > num_points:
                consumption = consumption[:num_points]
                production = production[:num_points]
            else:
                consumption = np.pad(consumption, (0, num_points - len(consumption)))
                production = np.pad(production, (0, num_points - len(production)))

        community_consumption += consumption
        community_production += production

        profile_meta = profile_meta or {}
        source = profile_meta.get("source", "mock")
        source_counts[source] = source_counts.get(source, 0) + 1
        confidence_values.append(max(0.0, min(1.0, _to_float(profile_meta.get("quality_score"), MOCK_PROFILE_CONFIDENCE))))

    total_consumption_kwh = float(community_consumption.sum() * 0.25)
    total_production_kwh = float(community_production.sum() * 0.25)
    if total_consumption_kwh <= 0:
        result = {
            "autarky_score": 0.0,
            "total_consumption_kwh": 0.0,
            "total_production_kwh": total_production_kwh,
            "confidence_percent": 0.0,
            "profile_data_mix": "mock",
            "cache_hit": False,
            "source_counts": source_counts,
        }
        _cache_set(cache_backend, cache_key, result, cache_ttl_seconds)
        return result

    net_load_kw = community_consumption - community_production
    energy_kwh = net_load_kw * 0.25
    grid_import_kwh = float(energy_kwh[energy_kwh > 0].sum())
    autarky_score = (total_consumption_kwh - grid_import_kwh) / total_consumption_kwh

    real_count = source_counts.get("real", 0)
    mock_like_count = source_counts.get("mock", 0) + source_counts.get("provided", 0)
    if real_count > 0 and mock_like_count == 0:
        profile_data_mix = "real"
    elif real_count > 0 and mock_like_count > 0:
        profile_data_mix = "hybrid"
    else:
        profile_data_mix = "mock"

    confidence_percent = float(np.mean(confidence_values) * 100.0) if confidence_values else 0.0
    result = {
        "autarky_score": float(autarky_score),
        "total_consumption_kwh": total_consumption_kwh,
        "total_production_kwh": total_production_kwh,
        "confidence_percent": round(confidence_percent, 2),
        "profile_data_mix": profile_data_mix,
        "cache_hit": False,
        "source_counts": source_counts,
    }
    _cache_set(cache_backend, cache_key, result, cache_ttl_seconds)
    return result


def calculate_community_autarky(
    community_buildings_df,
    all_profiles=None,
    profile_provider=None,
    cache_backend=None,
    cache_key=None,
    cache_ttl_seconds=86400,
    strategy="hybrid",
    window=None,
):
    """Compatibility wrapper returning legacy tuple."""
    details = calculate_community_autarky_details(
        community_buildings_df=community_buildings_df,
        all_profiles=all_profiles,
        profile_provider=profile_provider,
        cache_backend=cache_backend,
        cache_key=cache_key,
        cache_ttl_seconds=cache_ttl_seconds,
        strategy=strategy,
        window=window,
    )
    return (
        details.get("autarky_score", 0.0),
        details.get("total_consumption_kwh", 0.0),
        details.get("total_production_kwh", 0.0),
    )


def get_cluster_info(
    community_df,
    cluster_id,
    city_id=None,
    sim_version=SIM_VERSION,
    cache_backend=None,
    cache_ttl_seconds=86400,
    profile_provider=None,
    strategy="hybrid",
    window=None,
):
    """Prepare cluster details for API responses."""
    cache_key = build_community_signature(community_df, city_id=city_id, sim_version=sim_version)
    details = calculate_community_autarky_details(
        community_buildings_df=community_df,
        all_profiles=None,
        profile_provider=profile_provider,
        cache_backend=cache_backend,
        cache_key=cache_key,
        cache_ttl_seconds=cache_ttl_seconds,
        strategy=strategy,
        window=window,
    )

    members = []
    for _, row in community_df.iterrows():
        members.append({
            "building_id": row.get("building_id"),
            "lat": row.get("lat"),
            "lon": row.get("lon"),
        })

    return {
        "community_id": int(cluster_id) if isinstance(cluster_id, (int, np.integer)) else cluster_id,
        "num_members": len(community_df),
        "building_ids": list(community_df["building_id"]),
        "members": members,
        "autarky_percent": details.get("autarky_score", 0.0) * 100.0,
        "total_consumption_mwh": details.get("total_consumption_kwh", 0.0) / 1000.0,
        "total_production_mwh": details.get("total_production_kwh", 0.0) / 1000.0,
        "confidence_percent": details.get("confidence_percent", 0.0),
        "profile_data_mix": details.get("profile_data_mix", "mock"),
        "cache_hit": details.get("cache_hit", False),
        "cache_key": cache_key,
    }


def find_optimal_communities(
    building_data_df,
    radius_meters=150,
    min_community_size=3,
    city_id=None,
    sim_version=SIM_VERSION,
    cache_backend=None,
    cache_ttl_seconds=86400,
    profile_provider=None,
    strategy="hybrid",
    window=None,
):
    """
    Main ML function (DBSCAN + simulation).
    Returns ranked results and DataFrame with cluster assignments.
    """
    if building_data_df.empty or len(building_data_df) < min_community_size:
        print(f"[ML] Zu wenig Daten für Clustering (min. {min_community_size} benötigt, {len(building_data_df)} vorhanden).")
        if not building_data_df.empty:
            result_df = pd.DataFrame(building_data_df.to_dict("records"))
            result_df["cluster"] = -1
            return [], result_df
        return [], building_data_df

    print(f"[ML] Starte ML-Clustering (DBSCAN) für {len(building_data_df)} Gebäude...")
    working_df = pd.DataFrame(building_data_df.to_dict("records"))
    coords = working_df[["lat", "lon"]].values
    coords_rad = np.radians(coords)

    earth_radius_m = 6371e3
    eps_rad = radius_meters / earth_radius_m
    dbscan = DBSCAN(
        eps=eps_rad,
        min_samples=min_community_size,
        algorithm="ball_tree",
        metric="haversine",
    ).fit(coords_rad)
    working_df["cluster"] = dbscan.labels_

    num_clusters = len(set(dbscan.labels_)) - (1 if -1 in dbscan.labels_ else 0)
    print(f"[ML] DBSCAN fand {num_clusters} potenzielle Gemeinschaften.")

    print("[ML] Simuliere Autarkie für jeden Cluster...")
    results = []
    for cluster_id in sorted(set(dbscan.labels_)):
        if cluster_id == -1:
            continue
        cluster_mask = working_df["cluster"] == cluster_id
        community_buildings_df = pd.DataFrame(working_df[cluster_mask].to_dict("records"))
        cluster_info = get_cluster_info(
            community_df=community_buildings_df,
            cluster_id=cluster_id,
            city_id=city_id,
            sim_version=sim_version,
            cache_backend=cache_backend,
            cache_ttl_seconds=cache_ttl_seconds,
            profile_provider=profile_provider,
            strategy=strategy,
            window=window,
        )
        results.append(cluster_info)

    ranked_results = sorted(results, key=lambda x: x["autarky_percent"], reverse=True)
    result_df = working_df.copy()

    if "building_id" not in result_df.columns and "building_id" in building_data_df.columns:
        building_data_df_reset = building_data_df.reset_index(drop=True)
        if len(result_df) == len(building_data_df_reset):
            result_df["building_id"] = building_data_df_reset["building_id"].values

    return ranked_results, result_df


if __name__ == "__main__":
    print("Dieses Skript ist ein Modul und sollte von app.py importiert werden.")
    print("Es kann nicht direkt ausgeführt werden.")
