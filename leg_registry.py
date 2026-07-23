# SPDX-License-Identifier: AGPL-3.0-or-later
"""Open LEG registry: public directory, self-service submission, and claim flow.

See docs/leg-registry.md for the product contract — in particular the
honesty boundary: a published entry is a moderated self-report, never a
verified grid-topology eligibility signal.
"""

import logging
import secrets

from flask import Blueprint, abort, jsonify, render_template, request

import database as db
import email_utils
import registry_intake
from cantons import SWISS_CANTON_OPTIONS

CLAIM_TOKEN_TTL_SECONDS = 24 * 60 * 60
VERIFICATION_TOKEN_TTL_SECONDS = 14 * 24 * 60 * 60
VERIFICATION_STALE_DAYS = 90

logger = logging.getLogger(__name__)

registry_bp = Blueprint("registry", __name__)

LEG_STATUS_OPTIONS = [
    ("", "Alle Status"),
    ("planung", "In Planung"),
    ("gruendung", "In Gründung"),
    ("aktiv", "Aktiv"),
    ("pausiert", "Pausiert"),
]


def _clean_param(name):
    value = (request.args.get(name) or "").strip()
    if not value or value.upper() == "ALL":
        return None
    return value


@registry_bp.route("/leg-verzeichnis")
def liste():
    kanton = _clean_param("kanton")
    plz = _clean_param("plz")
    leg_status = _clean_param("leg_status")
    q = _clean_param("q")

    entries = db.list_registry_entries(
        kanton=kanton.upper() if kanton else None,
        plz=plz,
        leg_status=leg_status,
        q=q,
    )

    return render_template(
        "leg_verzeichnis/liste.html",
        entries=entries,
        kanton=kanton or "",
        plz=plz or "",
        leg_status=leg_status or "",
        q=q or "",
        canton_options=SWISS_CANTON_OPTIONS,
        leg_status_options=LEG_STATUS_OPTIONS,
        site_url=request.url_root.rstrip("/"),
        canonical_path="/leg-verzeichnis",
    )


@registry_bp.route("/leg-verzeichnis/<slug>")
def detail(slug):
    entry = db.get_registry_entry_by_slug(slug)
    if not entry or entry.get("moderation_status") != "published":
        abort(404)

    return render_template(
        "leg_verzeichnis/detail.html",
        entry=entry,
        site_url=request.url_root.rstrip("/"),
        canonical_path=f"/leg-verzeichnis/{slug}",
    )


@registry_bp.route("/leg-verzeichnis/eintragen", methods=["GET", "POST"])
def eintragen():
    if request.method == "GET":
        return render_template(
            "leg_verzeichnis/eintragen.html",
            canton_options=SWISS_CANTON_OPTIONS,
            leg_status_options=LEG_STATUS_OPTIONS,
            site_url=request.url_root.rstrip("/"),
            canonical_path="/leg-verzeichnis/eintragen",
            error=None,
            form=None,
        )

    form = request.form
    result = registry_intake.submit(form, source="self_submitted")
    if result["error"]:
        return _eintragen_error(result["error"], form, status=result["status"])

    return render_template(
        "leg_verzeichnis/eintragen.html",
        canton_options=SWISS_CANTON_OPTIONS,
        leg_status_options=LEG_STATUS_OPTIONS,
        site_url=request.url_root.rstrip("/"),
        canonical_path="/leg-verzeichnis/eintragen",
        error=None,
        form=None,
        submitted=True,
    )


@registry_bp.route("/api/registry/publish", methods=["POST"])
def api_registry_publish():
    data = request.get_json(silent=True) or {}
    result = registry_intake.submit(data, source="self_hosted")
    if result["error"]:
        return jsonify({"error": result["error"]}), result["status"]
    return jsonify({"slug": result["slug"], "moderation_status": "pending"}), 201


def _eintragen_error(message, form, status):
    return (
        render_template(
            "leg_verzeichnis/eintragen.html",
            canton_options=SWISS_CANTON_OPTIONS,
            leg_status_options=LEG_STATUS_OPTIONS,
            site_url=request.url_root.rstrip("/"),
            canonical_path="/leg-verzeichnis/eintragen",
            error=message,
            form=form,
        ),
        status,
    )


@registry_bp.route("/leg-verzeichnis/<slug>/beanspruchen", methods=["GET", "POST"])
def beanspruchen(slug):
    entry = db.get_registry_entry_by_slug(slug)
    if not entry:
        abort(404)

    if request.method == "GET":
        return render_template(
            "leg_verzeichnis/beanspruchen.html",
            entry=entry,
            sent=False,
            site_url=request.url_root.rstrip("/"),
            canonical_path=f"/leg-verzeichnis/{slug}/beanspruchen",
        )

    token = secrets.token_urlsafe(32)
    db.set_registry_claim_token(entry["id"], token, ttl_seconds=CLAIM_TOKEN_TTL_SECONDS)

    confirm_url = f"{request.url_root.rstrip('/')}/leg-verzeichnis/beanspruchen/{token}"
    email_utils.send_email(
        entry["contact_email"],
        "Bestätigen Sie Ihren LEG-Eintrag bei OpenLEG",
        f'Um den Eintrag "{entry["name"]}" im OpenLEG-Verzeichnis zu '
        f"beanspruchen, öffnen Sie diesen Link (24 Stunden gültig):\n\n"
        f"{confirm_url}",
    )

    return render_template(
        "leg_verzeichnis/beanspruchen.html",
        entry=entry,
        sent=True,
        site_url=request.url_root.rstrip("/"),
        canonical_path=f"/leg-verzeichnis/{slug}/beanspruchen",
    )


@registry_bp.route("/leg-verzeichnis/beanspruchen/<token>")
def beanspruchen_bestaetigen(token):
    entry = db.get_registry_entry_by_claim_token(token)
    if not entry:
        abort(404)

    db.mark_registry_entry_claimed(entry["id"], entry["contact_email"])

    return render_template(
        "leg_verzeichnis/beanspruchen_bestaetigt.html",
        entry=entry,
        site_url=request.url_root.rstrip("/"),
        canonical_path=f"/leg-verzeichnis/{entry['slug']}",
    )


@registry_bp.route("/leg-verzeichnis/bestaetigen/<token>")
def bestaetigen(token):
    entry = db.get_registry_entry_by_verification_token(token)
    if not entry:
        abort(404)

    db.mark_registry_entry_verified(entry["id"])

    return render_template(
        "leg_verzeichnis/bestaetigt.html",
        entry=entry,
        site_url=request.url_root.rstrip("/"),
        canonical_path=f"/leg-verzeichnis/{entry['slug']}",
    )


@registry_bp.route("/leg-check")
def leg_check():
    """Honest pre-check: what is knowable about a municipality today.

    Resolves the municipality from locally cached public data (ElCom,
    Sonnendach via municipality_profiles) and shows existing registry
    entries. Never claims address-level or grid-topology eligibility.
    """
    q = _clean_param("q")
    profiles = db.search_municipality_profiles(q) if q else []

    profile = profiles[0] if len(profiles) == 1 else None
    operators = []
    entries = []
    if profile:
        tariffs = db.get_elcom_tariffs(profile["bfs_number"])
        seen = set()
        for tariff in tariffs:
            name = (tariff.get("operator_name") or "").strip()
            if name and name not in seen:
                seen.add(name)
                operators.append(name)
        entries = db.list_registry_entries(q=profile["name"])

    return render_template(
        "leg_verzeichnis/leg_check.html",
        q=q or "",
        profile=profile,
        matches=profiles if len(profiles) > 1 else [],
        operators=operators,
        entries=entries,
        site_url=request.url_root.rstrip("/"),
        canonical_path="/leg-check",
    )


def send_verification_nudges(base_url=""):
    """Email listed contacts whose entry is stale, asking them to reconfirm.

    Called from the /api/cron/verify-registry-entries cron endpoint. Skips
    entries where setting the token fails rather than emailing a dead link.
    """
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

        confirm_url = f"{base_url}/leg-verzeichnis/bestaetigen/{token}"
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
    """Add a vnb_plausible flag to each entry: a moderator hint, not a verdict.

    None when there's nothing to check (no bfs_number or no self-reported
    vnb_name). Otherwise True/False based on a case-insensitive substring
    match against ElCom's operator_name(s) for that municipality. This is a
    plausibility signal only — see docs/leg-registry.md's honesty boundary:
    it never confirms grid-topology eligibility.
    """
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
