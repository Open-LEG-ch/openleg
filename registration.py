# SPDX-License-Identifier: AGPL-3.0-or-later
"""Registration validation and orchestration."""

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

CONSENT_VERSION = "2026-01-01"
ALLOWED_ROLES = {
    "owner",
    "tenant",
    "solar_producer",
    "local_business",
    "municipality_organisation",
}


@dataclass(frozen=True, kw_only=True)
class RegistrationDeps:
    db: Any
    security: Any
    app_base_url: str
    thread: Callable[..., Any] = threading.Thread
    send_confirmation_email: Callable[..., Any]
    find_provisional_matches: Callable[..., Any]
    collect_building_locations: Callable[..., Any]


class RegistrationError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def coerce_bool(value):
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
        "share_with_neighbors": coerce_bool(consents.get("share_with_neighbors")),
        "share_with_utility": False,
        "updates_opt_in": coerce_bool(consents.get("updates_opt_in")),
        "consent_version": consents.get("consent_version") or CONSENT_VERSION,
        "consent_timestamp": time.time(),
    }


def parse_roles(raw_roles):
    if raw_roles is None:
        return []
    if not isinstance(raw_roles, list):
        raise RegistrationError("Bitte wählen Sie gültige Rollen aus.")
    roles = []
    for raw_role in raw_roles:
        role = raw_role.strip() if isinstance(raw_role, str) else ""
        if role not in ALLOWED_ROLES:
            raise RegistrationError("Bitte wählen Sie gültige Rollen aus.")
        if role not in roles:
            roles.append(role)
    return roles


def register(data, *, city_id, user_type, deps: RegistrationDeps):
    db = deps.db
    security = deps.security
    phone = (data.get("phone") or "").strip()
    email = (data.get("email") or "").strip()
    profile = data.get("profile")
    referral_code = (data.get("referral_code") or "").strip()
    roles = parse_roles(data.get("roles"))
    raw_has_solar = data.get("has_solar")
    has_solar = coerce_bool(raw_has_solar) if raw_has_solar is not None else None

    referrer_id = None
    if referral_code:
        referrer = db.get_building_by_referral_code(referral_code)
        if referrer:
            referrer_id = referrer.get("building_id")

    is_valid_email, normalized_email, email_error = security.validate_email_address(
        email
    )
    if not is_valid_email:
        raise RegistrationError(email_error)
    email = normalized_email

    if phone:
        is_valid_phone, normalized_phone, phone_error = security.validate_phone(phone)
        if not is_valid_phone:
            raise RegistrationError(phone_error)
        phone = normalized_phone

    if not profile:
        raise RegistrationError("Profildaten fehlen.")
    building_id = profile.get("building_id")
    is_valid_id, id_error = security.validate_building_id(building_id)
    if not is_valid_id:
        raise RegistrationError(id_error)

    lat = profile.get("lat")
    lon = profile.get("lon")
    is_valid_coords, coords_error = security.validate_coordinates(lat, lon)
    if not is_valid_coords:
        raise RegistrationError(coords_error)

    consents = parse_consents(data.get("consents"))
    verification_token = str(uuid.uuid4())
    try:
        saved = db.save_building(
            building_id=building_id,
            email=email,
            profile=profile,
            consents=consents,
            user_type=user_type,
            phone=phone,
            referrer_id=referrer_id,
            city_id=city_id,
            roles=roles,
            has_solar=has_solar,
            verified=False,
            verification_token=verification_token,
        )
    except db.VerifiedRegistrationConflict as error:
        raise RegistrationError(
            "Für dieses Gebäude besteht bereits eine bestätigte Anmeldung "
            "mit einer anderen E-Mail-Adresse.",
            status=409,
        ) from error
    if not saved:
        raise RegistrationError(
            "Die Interessenmeldung konnte nicht gespeichert werden.", status=503
        )

    verification_url = f"{deps.app_base_url}/confirm/{verification_token}"

    thread = deps.thread
    thread(
        target=deps.send_confirmation_email,
        args=(email, verification_url, building_id, profile.get("address", "")),
        daemon=True,
    ).start()
    db.track_event("registration", building_id, {"type": user_type, "city_id": city_id})

    cluster_info = deps.find_provisional_matches(profile)
    locations = deps.collect_building_locations(
        city_id=city_id,
        bfs_number=profile.get("bfs_number"),
        exclude_building_id=building_id,
    )
    referral_link = None
    ref_code = db.get_referral_code(building_id)
    if ref_code:
        referral_link = f"{deps.app_base_url}/?ref={ref_code}"

    payload = {
        "buildings": locations,
        "match_found": bool(cluster_info),
        "verification_email_sent": True,
        "referral_link": referral_link,
    }
    if cluster_info:
        payload["cluster_info"] = cluster_info
    return payload
