# SPDX-License-Identifier: AGPL-3.0-or-later
"""Neighbour read policy: anonymity radius, jittered map locations, provisional match summary."""

import hashlib
import math

import numpy as np
import pandas as pd

import database as db
import ml_models

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


def collect_building_locations(city_id=None, exclude_building_id=None):
    """Get all verified building locations with jittered coordinates."""
    buildings = db.get_all_buildings(city_id=city_id)
    locations = []
    for b in buildings:
        if exclude_building_id and b.get("building_id") == exclude_building_id:
            continue
        lat = b.get("lat")
        lon = b.get("lon")
        if lat is None or lon is None:
            continue
        jlat, jlon = jitter_coordinates(
            float(lat), float(lon), seed=b.get("building_id")
        )
        locations.append(
            {"lat": jlat, "lon": jlon, "type": b.get("user_type", "anonymous")}
        )
    return locations


def find_provisional_matches(new_profile):
    """Fast provisional match search (distance only, no DBSCAN)."""
    profiles = db.get_all_building_profiles()
    if not profiles:
        return None

    new_coords = (new_profile["lat"], new_profile["lon"])
    provisional = [new_profile]

    for p in profiles:
        dist = ml_models.calculate_distance(
            new_coords[0], new_coords[1], float(p["lat"]), float(p["lon"])
        )
        if dist <= 150:
            provisional.append(p)

    if len(provisional) < 2:
        return None

    community_df = pd.DataFrame(provisional)
    autarky_score, _, _ = ml_models.calculate_community_autarky(community_df, None)

    return {
        "community_id": "provisional",
        "num_members": len(provisional),
        "autarky_percent": autarky_score * 100,
    }
