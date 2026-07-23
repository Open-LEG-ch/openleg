# SPDX-License-Identifier: AGPL-3.0-or-later
"""Validate and persist one registry submission from any transport."""

import re

import database as db
import security_utils

VALID_LEG_STATUSES = {"planung", "gruendung", "aktiv", "pausiert"}
_UMLAUT_MAP = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "ae", "Ö": "oe", "Ü": "ue", "ß": "ss"}
)


def _unique_slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.translate(_UMLAUT_MAP).lower()).strip("-")
    base = base or "leg"
    slug = base
    suffix = 2
    while db.get_registry_entry_by_slug(slug) is not None:
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def submit(payload, source: str) -> dict:
    """Normalize, validate and save one pending registry entry."""
    name = (payload.get("name") or "").strip()
    contact_email = (payload.get("contact_email") or "").strip()
    if not name or not contact_email:
        return {"error": "Name und Kontakt-E-Mail sind erforderlich.", "status": 400}

    valid, normalized_email, email_error = security_utils.validate_email_address(
        contact_email
    )
    if not valid:
        return {"error": email_error, "status": 400}

    leg_status = (payload.get("leg_status") or "planung").strip()
    if leg_status not in VALID_LEG_STATUSES:
        leg_status = "planung"

    try:
        member_count = int(payload.get("member_count_estimate") or 0)
        member_count_estimate = member_count if member_count > 0 else None
    except (TypeError, ValueError):
        member_count_estimate = None

    slug = _unique_slug(name)
    kanton = (payload.get("kanton") or "").strip().upper()
    saved = db.save_registry_entry(
        slug=slug,
        name=name,
        contact_email=normalized_email,
        kanton=kanton,
        plz=(payload.get("plz") or "").strip(),
        ort=(payload.get("ort") or "").strip(),
        vnb_name=(payload.get("vnb_name") or "").strip(),
        member_count_estimate=member_count_estimate,
        leg_status=leg_status,
        description=(payload.get("description") or "").strip(),
        website_url=(payload.get("website_url") or "").strip(),
        source=source,
    )
    if not saved:
        return {"error": "Der Eintrag konnte nicht gespeichert werden.", "status": 500}

    db.track_event(
        "registry_entry_submitted",
        data={"slug": slug, "kanton": kanton, "source": source},
    )
    return {"error": None, "slug": slug, "moderation_status": "pending"}
