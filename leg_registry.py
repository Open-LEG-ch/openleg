# SPDX-License-Identifier: AGPL-3.0-or-later
"""Open LEG registry: public directory, self-service submission, and claim flow.

See docs/leg-registry.md for the product contract — in particular the
honesty boundary: a published entry is a moderated self-report, never a
verified grid-topology eligibility signal.
"""

import logging
import re

from flask import Blueprint, abort, render_template, request

import database as db
import security_utils
from cantons import SWISS_CANTON_OPTIONS

logger = logging.getLogger(__name__)

registry_bp = Blueprint("registry", __name__)

LEG_STATUS_OPTIONS = [
    ("", "Alle Status"),
    ("planung", "In Planung"),
    ("gruendung", "In Gründung"),
    ("aktiv", "Aktiv"),
    ("pausiert", "Pausiert"),
]

_UMLAUT_MAP = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "ae", "Ö": "oe", "Ü": "ue", "ß": "ss"}
)


def _clean_param(name):
    value = (request.args.get(name) or "").strip()
    if not value or value.upper() == "ALL":
        return None
    return value


def _slugify(name: str) -> str:
    base = name.translate(_UMLAUT_MAP).lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return base or "leg"


def _unique_slug(name: str) -> str:
    base = _slugify(name)
    slug = base
    suffix = 2
    while db.get_registry_entry_by_slug(slug) is not None:
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


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
    name = (form.get("name") or "").strip()
    contact_email = (form.get("contact_email") or "").strip()

    if not name or not contact_email:
        return _eintragen_error(
            "Name und Kontakt-E-Mail sind erforderlich.", form, status=400
        )

    is_valid, normalized_email, email_error = security_utils.validate_email_address(
        contact_email
    )
    if not is_valid:
        return _eintragen_error(email_error, form, status=400)

    leg_status = (form.get("leg_status") or "planung").strip()
    if leg_status not in {code for code, _ in LEG_STATUS_OPTIONS if code}:
        leg_status = "planung"

    member_count_raw = (form.get("member_count_estimate") or "").strip()
    member_count_estimate = (
        int(member_count_raw) if member_count_raw.isdigit() else None
    )
    kanton = (form.get("kanton") or "").strip().upper()

    slug = _unique_slug(name)
    saved = db.save_registry_entry(
        slug=slug,
        name=name,
        contact_email=normalized_email,
        kanton=kanton,
        plz=(form.get("plz") or "").strip(),
        ort=(form.get("ort") or "").strip(),
        vnb_name=(form.get("vnb_name") or "").strip(),
        member_count_estimate=member_count_estimate,
        leg_status=leg_status,
        description=(form.get("description") or "").strip(),
        website_url=(form.get("website_url") or "").strip(),
        source="self_submitted",
    )
    if not saved:
        return _eintragen_error(
            "Der Eintrag konnte nicht gespeichert werden. Bitte später erneut versuchen.",
            form,
            status=500,
        )

    db.track_event("registry_entry_submitted", data={"slug": slug, "kanton": kanton})

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
