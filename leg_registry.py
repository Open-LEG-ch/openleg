# SPDX-License-Identifier: AGPL-3.0-or-later
"""Open LEG registry: public directory, self-service submission, and claim flow.

See docs/leg-registry.md for the product contract — in particular the
honesty boundary: a published entry is a moderated self-report, never a
verified grid-topology eligibility signal.
"""

import logging

from flask import Blueprint, abort, render_template, request

import database as db
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
