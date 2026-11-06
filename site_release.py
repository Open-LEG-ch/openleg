# SPDX-License-Identifier: AGPL-3.0-or-later
"""Opt-in entry to the complete site; formation facts require actual evidence."""

from flask import Blueprint, abort, current_app, render_template

site_bp = Blueprint("site", __name__)


@site_bp.before_request
def require_complete_site():
    if current_app.config.get("SITE_RELEASE_ENABLED") is not True:
        abort(404)


@site_bp.get("/verein")
def verein():
    return render_template("verein/index.html", site_url=current_app.config["SITE_URL"])


def template_context():
    return {
        "site_release_enabled": current_app.config.get("SITE_RELEASE_ENABLED") is True
    }
