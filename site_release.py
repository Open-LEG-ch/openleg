# SPDX-License-Identifier: AGPL-3.0-or-later
"""Opt-in entry to the complete site; formation facts require actual evidence."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, current_app, render_template

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


def formation_date():
    """Only configured actual past/present formation metadata changes the claim."""
    try:
        formed = date.fromisoformat(current_app.config.get("VEREIN_FORMATION_DATE", ""))
    except (ValueError, TypeError):
        return None
    return formed if formed <= datetime.now(ZoneInfo("Europe/Zurich")).date() else None


def template_context():
    return {
        "site_release_enabled": current_app.config.get("SITE_RELEASE_ENABLED") is True,
        "verein_formation_date": formation_date(),
    }
