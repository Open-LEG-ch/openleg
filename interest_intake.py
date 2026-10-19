# SPDX-License-Identifier: AGPL-3.0-or-later
"""Validation and orchestration for unresolved address interest."""

import re
import secrets
import uuid

import registration
from email_localization import gettext as _
from i18n import localized_url
from locale_policy import require_recipient_locale
from translation_markers import gettext_noop


class InterestIntakeError(Exception):
    """Validation failure with a message intended for the public response."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


def submit(data, *, db, security, base_url, send_email):
    if not isinstance(data, dict):
        raise InterestIntakeError(gettext_noop("Bitte senden Sie ein JSON-Objekt."))
    try:
        preferred_locale = require_recipient_locale(
            data.get("preferred_locale", "de_CH")
        )
    except ValueError as locale_error:
        raise InterestIntakeError(
            gettext_noop("Bitte wählen Sie eine gültige Sprache.")
        ) from locale_error
    email = (data.get("email") or "").strip()
    valid, email, error = security.validate_email_address(email)
    if not valid:
        raise InterestIntakeError(error)

    plz = str(data.get("plz") or "").strip()
    if not re.fullmatch(r"[1-9]\d{3}", plz):
        raise InterestIntakeError(
            gettext_noop("Bitte geben Sie eine gültige Schweizer PLZ an.")
        )
    municipality_name = security.sanitize_string(
        data.get("municipality_name") or "", max_length=120
    )
    if len(municipality_name) < 2:
        raise InterestIntakeError(gettext_noop("Bitte geben Sie Ihre Gemeinde an."))
    address = security.sanitize_string(data.get("address") or "", max_length=200)
    try:
        roles = registration.parse_roles(data.get("roles"))
    except registration.RegistrationError as error:
        raise InterestIntakeError(error.message) from error
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
        **(
            {"preferred_locale": preferred_locale} if "preferred_locale" in data else {}
        ),
    )
    if not saved:
        raise InterestIntakeError(
            gettext_noop("Die Interessenmeldung konnte nicht gespeichert werden.")
        )

    verification_url = localized_url(
        f"{base_url.rstrip('/')}/interest/confirm/{verification_token}",
        preferred_locale,
    )
    send_email(
        email,
        _("OpenLEG: Interessenmeldung bestätigen", locale=preferred_locale),
        _(
            "Bestätigen Sie Ihre Interessenmeldung:\n\n%(verification_url)s\n\nErst danach wird Ihre Anmeldung anonym gezählt. Falls Sie sich nicht angemeldet haben, ignorieren Sie diese E-Mail.",
            verification_url=verification_url,
            locale=preferred_locale,
        ),
    )
    return {
        "accepted": True,
        "message": gettext_noop("Bitte bestätigen Sie Ihre E-Mail-Adresse."),
    }
