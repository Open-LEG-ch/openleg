# SPDX-License-Identifier: AGPL-3.0-or-later
"""Validation and orchestration for unresolved address interest."""

import re
import secrets
import uuid

import registration


class InterestIntakeError(Exception):
    pass


def submit(data, *, db, security, base_url, send_email):
    email = (data.get("email") or "").strip()
    valid, email, error = security.validate_email_address(email)
    if not valid:
        raise InterestIntakeError(error)

    plz = security.sanitize_string(str(data.get("plz") or ""), max_length=4)
    if not re.fullmatch(r"[1-9]\d{3}", plz):
        raise InterestIntakeError("Bitte geben Sie eine gültige Schweizer PLZ an.")
    municipality_name = security.sanitize_string(
        data.get("municipality_name") or "", max_length=120
    )
    if len(municipality_name) < 2:
        raise InterestIntakeError("Bitte geben Sie Ihre Gemeinde an.")
    address = security.sanitize_string(data.get("address") or "", max_length=200)
    try:
        roles = registration.parse_roles(data.get("roles"))
    except registration.RegistrationError as error:
        raise InterestIntakeError(str(error)) from error
    raw_has_solar = data.get("has_solar")
    has_solar = (
        registration.coerce_bool(raw_has_solar) if raw_has_solar is not None else None
    )

    matches = db.search_municipality_profiles(municipality_name, limit=10)
    municipality = next(
        (
            row
            for row in matches
            if (row.get("name") or "").casefold() == municipality_name.casefold()
        ),
        None,
    )
    bfs_number = municipality.get("bfs_number") if municipality else None
    canton = municipality.get("kanton") if municipality else None
    canonical_name = municipality.get("name") if municipality else municipality_name

    request_id = str(uuid.uuid4())
    verification_token = secrets.token_urlsafe(32)
    saved = db.save_coverage_request(
        request_id=request_id,
        email=email,
        address=address,
        plz=plz,
        municipality_name=canonical_name,
        canton=canton,
        bfs_number=bfs_number,
        roles=roles,
        has_solar=has_solar,
        verification_token=verification_token,
    )
    if not saved:
        raise InterestIntakeError(
            "Die Interessenmeldung konnte nicht gespeichert werden."
        )

    verification_url = f"{base_url.rstrip('/')}/interest/confirm/{verification_token}"
    send_email(
        email,
        "OpenLEG: Interessenmeldung bestätigen",
        "Bestätigen Sie Ihre Interessenmeldung:\n\n"
        f"{verification_url}\n\n"
        "Erst danach wird Ihre Anmeldung anonym gezählt. Falls Sie sich nicht "
        "angemeldet haben, ignorieren Sie diese E-Mail.",
    )
    return {
        "accepted": True,
        "message": "Bitte bestätigen Sie Ihre E-Mail-Adresse.",
    }
