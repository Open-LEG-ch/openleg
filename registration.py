# SPDX-License-Identifier: AGPL-3.0-or-later
"""Registration verbs and building-location helpers.

Moved here from app.py: parse_consents, jitter_coordinates,
collect_building_locations, send_confirmation_email, run_full_ml_task,
find_provisional_matches, send_confirmation_email.

Verbs: check_potential, register_anonymous, register_full.
"""

import hashlib
import logging
import math
import os
import threading
import time
import uuid

import numpy as np
import pandas as pd

import data_enricher
import database as db
import email_automation
import ml_models
import security_utils
from email_utils import send_email

logger = logging.getLogger(__name__)

CONSENT_VERSION = "2026-01-01"
ANONYMITY_RADIUS_METERS = 120
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "hallo@openleg.ch")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _coerce_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "ja", "on")
    return False


def parse_consents(raw_consents):
    consents = raw_consents or {}
    return {
        "share_with_neighbors": _coerce_bool(consents.get("share_with_neighbors")),
        "share_with_utility": _coerce_bool(consents.get("share_with_utility")),
        "updates_opt_in": _coerce_bool(consents.get("updates_opt_in")),
        "consent_version": consents.get("consent_version") or CONSENT_VERSION,
        "consent_timestamp": time.time(),
    }


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


# ---------------------------------------------------------------------------
# DB-backed helpers
# ---------------------------------------------------------------------------


def collect_building_locations(city_id=None, exclude_building_id=None):
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
    members = [
        {
            "building_id": p.get("building_id", ""),
            "lat": float(p["lat"]),
            "lon": float(p["lon"]),
        }
        for p in provisional
    ]
    return {
        "community_id": "provisional",
        "num_members": len(members),
        "members": members,
        "autarky_percent": autarky_score * 100,
    }


def send_confirmation_email(email, unsubscribe_url, building_id=None, address=None):
    try:
        from flask import g

        try:
            city = getattr(g, "tenant", {}).get("city_name", "Zürich")
            name = getattr(g, "tenant", {}).get("platform_name", "OpenLEG")
        except RuntimeError:
            city = "Zürich"
            name = "OpenLEG"
    except ImportError:
        city = "Zürich"
        name = "OpenLEG"
    subject = f"{name}: Registrierung bestätigt"
    message_body = (
        f"Willkommen bei {name}!\n\n"
        f"Sie sind jetzt für eine Lokale Elektrizitätsgemeinschaft (LEG) in {city} registriert.\n\n"
        "Wir informieren Sie per E-Mail, sobald sich neue Interessenten in Ihrer Zone anmelden.\n\n"
        f"Abmelden:\n{unsubscribe_url}\n\n"
        f"Ihr {name}-Team"
    )
    send_email(email, subject, message_body)


def run_full_ml_task(new_building_id=None, city_id=None):
    logger.info("[ML] Starting background clustering...")
    profiles = db.get_all_building_profiles(city_id=city_id)
    if len(profiles) < 2:
        logger.info("[ML] Not enough buildings for clustering.")
        return
    building_data = pd.DataFrame(profiles)
    ranked_communities, buildings_with_clusters = ml_models.find_optimal_communities(
        building_data, radius_meters=150, min_community_size=2
    )
    if "building_id" in buildings_with_clusters.columns:
        for _, row in buildings_with_clusters.iterrows():
            bid = row.get("building_id")
            cid = row.get("cluster", -1)
            if bid and cid >= 0:
                db.save_cluster(bid, cid)
    for community in ranked_communities:
        db.save_cluster_info(community["community_id"], community)
    logger.info(f"[ML] Clustering done: {len(ranked_communities)} clusters")


def _get_energy_profile(address: str):
    try:
        estimates, profiles = data_enricher.get_energy_profile_for_address(address)
        if not estimates:
            estimates, profiles = data_enricher.get_mock_energy_profile_for_address(
                address
            )
    except Exception:
        estimates, profiles = data_enricher.get_mock_energy_profile_for_address(address)
    return estimates, profiles


def _spawn_post_registration_threads(
    email: str,
    unsubscribe_url: str,
    building_id: str,
    address: str,
    city_id: str,
):
    threading.Thread(
        target=send_confirmation_email,
        args=(email, unsubscribe_url, building_id, address),
        daemon=True,
    ).start()
    threading.Thread(
        target=run_full_ml_task, args=(building_id, city_id), daemon=True
    ).start()
    threading.Thread(
        target=email_automation.schedule_sequence_for_user,
        args=(building_id, email),
        daemon=True,
    ).start()


# ---------------------------------------------------------------------------
# Verbs
# ---------------------------------------------------------------------------


def check_potential(address: str) -> dict:
    """Check energy potential for an address. Returns result dict.

    On error includes ``_status`` with HTTP status code.
    """
    estimates, _profiles = _get_energy_profile(address)
    if not estimates:
        return {"error": "Adresse konnte nicht analysiert werden.", "_status": 404}
    cluster_info = find_provisional_matches(estimates)
    if not cluster_info:
        return {
            "potential": False,
            "message": "Keine direkten Partner gefunden.",
            "profile_summary": estimates,
        }
    return {
        "potential": True,
        "message": "Partner gefunden!",
        "cluster_info": cluster_info,
        "profile_summary": estimates,
    }


def _register(
    payload: dict,
    *,
    user_type: str,
    city_id: str,
    app_base_url: str,
) -> dict:
    phone = (payload.get("phone") or "").strip()
    email = (payload.get("email") or "").strip()
    profile = payload.get("profile")
    referral_code = (payload.get("referral_code") or "").strip()

    referrer_id = None
    if referral_code:
        referrer = db.get_building_by_referral_code(referral_code)
        if referrer:
            referrer_id = referrer.get("building_id")

    is_valid_email, normalized_email, email_error = (
        security_utils.validate_email_address(email)
    )
    if not is_valid_email:
        return {"error": email_error, "_status": 400}
    email = normalized_email

    if phone:
        is_valid_phone, normalized_phone, phone_error = security_utils.validate_phone(
            phone
        )
        if not is_valid_phone:
            return {"error": phone_error, "_status": 400}
        phone = normalized_phone

    if not profile:
        return {"error": "Profildaten fehlen.", "_status": 400}

    building_id = profile.get("building_id")
    is_valid_id, id_error = security_utils.validate_building_id(building_id)
    if not is_valid_id:
        return {"error": id_error, "_status": 400}

    lat = profile.get("lat")
    lon = profile.get("lon")
    is_valid_coords, coords_error = security_utils.validate_coordinates(lat, lon)
    if not is_valid_coords:
        return {"error": coords_error, "_status": 400}

    consents = parse_consents(payload.get("consents"))
    if not consents.get("share_with_neighbors") or not consents.get(
        "share_with_utility"
    ):
        return {"error": "Bitte stimmen Sie der Datenweitergabe zu.", "_status": 400}

    db.save_building(
        building_id=building_id,
        email=email,
        profile=profile,
        consents=consents,
        user_type=user_type,
        phone=phone,
        referrer_id=referrer_id,
        city_id=city_id,
    )

    unsub_token = str(uuid.uuid4())
    db.save_token(unsub_token, building_id, "unsubscribe")
    unsubscribe_url = f"{app_base_url}/unsubscribe/{unsub_token}"

    _spawn_post_registration_threads(
        email, unsubscribe_url, building_id, profile.get("address", ""), city_id
    )

    db.track_event("registration", building_id, {"type": user_type, "city_id": city_id})

    cluster_info = find_provisional_matches(profile)
    locations = collect_building_locations(
        city_id=city_id, exclude_building_id=building_id
    )
    referral_link = None
    ref_code = db.get_referral_code(building_id)
    if ref_code:
        referral_link = f"{app_base_url}/?ref={ref_code}"

    result = {
        "buildings": locations,
        "match_found": bool(cluster_info),
        "verification_email_sent": True,
        "referral_link": referral_link,
    }
    if cluster_info:
        result["cluster_info"] = cluster_info
    return result


def register_anonymous(payload: dict, *, city_id: str, app_base_url: str) -> dict:
    return _register(
        payload, user_type="anonymous", city_id=city_id, app_base_url=app_base_url
    )


def register_full(payload: dict, *, city_id: str, app_base_url: str) -> dict:
    return _register(
        payload, user_type="registered", city_id=city_id, app_base_url=app_base_url
    )
