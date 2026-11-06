# SPDX-License-Identifier: AGPL-3.0-or-later
"""Opt-in entry to the complete site; formation facts require actual evidence."""

from datetime import date, datetime
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, current_app, render_template, request

import database as db
from cantons import SWISS_CANTONS
from i18n import gettext as _
from i18n import localized_url
from registry_intake import VALID_LEG_STATUSES, normalize_website_url

site_bp = Blueprint("site", __name__)


@site_bp.before_request
def require_complete_site():
    if current_app.config.get("SITE_RELEASE_ENABLED") is not True:
        abort(404)


@site_bp.get("/verein")
def verein():
    return render_template("verein/index.html", site_url=current_app.config["SITE_URL"])


@site_bp.get("/software")
def software():
    return render_template(
        "release/software.html", site_url=current_app.config["SITE_URL"]
    )


@site_bp.get("/wissen")
@site_bp.get("/wissen/strommodelle")
@site_bp.get("/wissen/glossar")
@site_bp.get("/dokumente")
def knowledge():
    return render_template(
        "release/knowledge.html", site_url=current_app.config["SITE_URL"]
    )


@site_bp.get("/community")
@site_bp.get("/mitmachen")
@site_bp.get("/kontakt")
def community():
    return render_template(
        "release/community.html", site_url=current_app.config["SITE_URL"]
    )


@site_bp.get("/leg-verzeichnis")
@site_bp.get("/leg-verzeichnis/<slug>")
def directory(slug=None):
    filters = {
        key: request.args.get(key, "").strip()
        for key in ("q", "kanton", "plz", "leg_status", "bfs")
    }
    check_q = request.args.get("check_q", "").strip()[:200]
    check_url = (
        "/leg-check?" + urlencode({"q": check_q, "bfs": filters["bfs"]})
        if check_q and filters["bfs"]
        else "/leg-check"
    )
    errors = {}
    page = request.args.get("page", "1")
    try:
        page = int(page)
        if not 1 <= page <= 10000:
            raise ValueError
    except ValueError:
        page = 1
        errors["page"] = _(
            "Die Ergebnisseite ist ungültig. Starten Sie die Suche erneut."
        )
    if len(filters["q"]) > 200:
        errors["q"] = _("Verwenden Sie höchstens 200 Zeichen für die Suche.")
    if filters["kanton"] and filters["kanton"] not in SWISS_CANTONS:
        errors["kanton"] = _("Wählen Sie einen Kanton aus der Liste.")
    if filters["plz"] and (
        len(filters["plz"]) != 4
        or not filters["plz"].isascii()
        or not filters["plz"].isdigit()
    ):
        errors["plz"] = _("Geben Sie eine vierstellige PLZ ein.")
    if filters["leg_status"] and filters["leg_status"] not in VALID_LEG_STATUSES:
        errors["leg_status"] = _("Wählen Sie einen Status aus der Liste.")
    try:
        bfs = int(filters["bfs"]) if filters["bfs"] else None
        if bfs is not None and not 1 <= bfs <= 999999:
            raise ValueError
    except ValueError:
        bfs = None
        errors["bfs"] = _("Geben Sie eine gültige BFS-Gemeindenummer ein.")
    if errors:
        return render_template(
            "leg_verzeichnis/liste.html",
            entries=[],
            filters=filters,
            page=1,
            error=_("Bitte prüfen Sie die Suchangaben."),
            errors=errors,
            check_url=check_url,
            check_q=check_q,
            unavailable=False,
            canton_options=sorted(SWISS_CANTONS),
            site_url=current_app.config["SITE_URL"],
            canonical_path="/leg-verzeichnis",
            search_noindex=True,
        ), 400

    def directory_url(entry_slug=None, target_page=None):
        args = {key: value for key, value in filters.items() if value}
        if check_q:
            args["check_q"] = check_q
        args["page"] = page if target_page is None else target_page
        path = "/leg-verzeichnis" + (
            "/" + quote(entry_slug, safe="") if entry_slug else ""
        )
        return localized_url(path + "?" + urlencode(args))

    context = {
        "filters": filters,
        "page": page,
        "error": None,
        "errors": errors,
        "check_url": check_url,
        "check_q": check_q,
        "unavailable": False,
        "canton_options": sorted(SWISS_CANTONS),
        "site_url": current_app.config["SITE_URL"],
        "canonical_path": request.path,
        "directory_url": directory_url,
        "search_noindex": any(filters.values()) or page > 1,
    }
    search = {key: value for key, value in filters.items() if value and key != "bfs"}
    if bfs is not None:
        search["bfs_number"] = bfs
    try:
        result = db.read_public_registry(
            filters=search if slug is None else {},
            slug=slug,
            page=page if slug is None else 1,
        )
    except db.PublicRegistryUnavailable:
        context["unavailable"] = True
        context["search_noindex"] = True
        return render_template("leg_verzeichnis/liste.html", entries=[], **context), 503
    for entry in result["entries"]:
        entry["website_url"] = normalize_website_url(entry["website_url"])
    if slug is not None:
        if not result["entries"]:
            abort(404)
        return render_template(
            "leg_verzeichnis/detail.html", entry=result["entries"][0], **context
        )
    return render_template("leg_verzeichnis/liste.html", **result, **context)


def formation_date():
    """Only configured actual past/present formation metadata changes the claim."""
    try:
        formed = date.fromisoformat(current_app.config.get("VEREIN_FORMATION_DATE", ""))
    except (ValueError, TypeError):
        return None
    return formed if formed <= datetime.now(ZoneInfo("Europe/Zurich")).date() else None


def accepting_applications():
    config = current_app.config
    return (
        config.get("VEREIN_ACCEPTING_APPLICATIONS") is True
        and formation_date() is not None
        and all(
            config.get(key)
            for key in (
                "VEREIN_CONTACT_ADDRESS",
                "VEREIN_PRIVACY_VERSION",
                "VEREIN_STATUTES_URL",
                "VEREIN_DATA_CONTROLLER",
            )
        )
    )


def template_context():
    return {
        "site_release_enabled": current_app.config.get("SITE_RELEASE_ENABLED") is True,
        "verein_formation_date": formation_date(),
        "verein_accepting_applications": accepting_applications(),
    }
