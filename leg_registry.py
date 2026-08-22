# SPDX-License-Identifier: AGPL-3.0-or-later
"""LEG registry moderation and verification operations."""

import secrets

from flask import Blueprint, jsonify, request

import database as db
import email_utils
import registry_intake

VERIFICATION_TOKEN_TTL_SECONDS = 14 * 24 * 60 * 60
VERIFICATION_STALE_DAYS = 90
registry_api_bp = Blueprint("registry_api", __name__)


@registry_api_bp.post("/api/registry/publish")
def api_registry_publish():
    """Accept self-hosted registry submissions for moderator review."""
    result = registry_intake.submit(
        request.get_json(silent=True) or {}, source="self_hosted"
    )
    if result["error"]:
        return jsonify({"error": result["error"]}), result["status"]
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

        confirm_url = f"{base_url}/registry/verify/{token}"
        email_utils.send_email(
            entry["contact_email"],
            "Ist Ihr LEG-Eintrag bei OpenLEG noch aktuell?",
            f'Bitte bestätigen Sie, dass der Eintrag "{entry["name"]}" im '
            f"OpenLEG-Verzeichnis noch aktuell ist (Link 14 Tage gültig):\n\n"
            f"{confirm_url}",
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
