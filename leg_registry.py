# SPDX-License-Identifier: AGPL-3.0-or-later
"""LEG registry moderation and verification operations."""

import secrets
import unicodedata

from flask import Blueprint, jsonify, request

import database as db
import email_utils
import registry_intake
from email_localization import gettext, recipient_locale
from i18n import gettext as _
from i18n import localized_url
from swiss_postcodes import postcodes_by_municipality

VERIFICATION_TOKEN_TTL_SECONDS = 14 * 24 * 60 * 60
VERIFICATION_STALE_DAYS = 90
registry_api_bp = Blueprint("registry_api", __name__)


@registry_api_bp.post("/api/registry/publish")
def api_registry_publish():
    """Accept self-hosted registry submissions for moderator review."""
    result = registry_intake.submit(
        request.get_json(silent=True) or {},
        source="self_hosted",
    )
    if result["error"]:
        message = result["error"]
        return jsonify({"error": _(message)}), result["status"]
    return jsonify({"slug": result["slug"], "moderation_status": "pending"}), 201


def send_verification_nudges(base_url=""):
    """Ask stale published registry contacts to reconfirm their entry."""
    candidates = db.get_registry_entries_needing_verification(
        stale_days=VERIFICATION_STALE_DAYS
    )
    result = {"candidates": len(candidates), "sent": 0, "errors": 0}

    for entry in candidates:
        token = secrets.token_urlsafe(32)
        token_set = db.set_registry_verification_token(
            entry["id"], token, ttl_seconds=VERIFICATION_TOKEN_TTL_SECONDS
        )
        if not token_set:
            result["errors"] += 1
            continue

        locale = recipient_locale(
            entry, entry["contact_email"], email_field="contact_email"
        )
        confirm_url = localized_url(
            f"{base_url}/registry/verify/{token}", locale=locale
        )
        email_utils.send_email(
            entry["contact_email"],
            gettext("Ist Ihr LEG-Eintrag bei OpenLEG noch aktuell?", locale=locale),
            gettext(
                'Bitte bestätigen Sie, dass der Eintrag "%(name)s" im OpenLEG-Verzeichnis '
                "noch aktuell ist (Link 14 Tage gültig):\n\n%(url)s",
                locale=locale,
                name=entry["name"],
                url=confirm_url,
            ),
        )
        result["sent"] += 1

    return result


def annotate_vnb_plausibility(entries):
    """Add a non-authoritative VNB plausibility hint for moderators."""
    annotated = []
    for entry in entries:
        entry = dict(entry)
        bfs_number = entry.get("bfs_number")
        vnb_name = (entry.get("vnb_name") or "").strip()
        if not bfs_number or not vnb_name:
            entry["vnb_plausible"] = None
        else:
            tariffs = db.get_elcom_tariffs(bfs_number)
            operator_names = [(t.get("operator_name") or "").lower() for t in tariffs]
            needle = vnb_name.lower()
            entry["vnb_plausible"] = any(
                needle in name or name in needle for name in operator_names if name
            )
        annotated.append(entry)
    return annotated


def municipality_candidates(entry: dict, profiles: list[dict]) -> list[dict]:
    """Find all exact municipality or postal-locality matches for the submission."""
    postcode = (entry.get("plz") or "").strip()
    locality = unicodedata.normalize("NFC", (entry.get("ort") or "").strip()).casefold()
    kanton = (entry.get("kanton") or "").strip().upper()
    if not (postcode or locality):
        return []

    postcodes = postcodes_by_municipality()
    matches = []
    for profile in profiles:
        bfs = profile["bfs_number"]
        profile_kanton = profile["kanton"] or ""
        if kanton and kanton != profile_kanton:
            continue
        postal_rows = [
            names
            for code, _share, names in postcodes.get(bfs, ())
            if not postcode or code == postcode
        ]
        if postcode and not postal_rows:
            continue
        names = {profile["name"].casefold()}
        for postal_names in postal_rows:
            names.update(name.casefold() for name in postal_names)
        if (
            locality
            and locality not in names
            and f"{locality} {profile_kanton.lower()}" not in names
        ):
            continue
        matches.append(profile)
    return matches
