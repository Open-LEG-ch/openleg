# SPDX-License-Identifier: AGPL-3.0-or-later
"""Authorize archive exercises inside a disposable product worker."""

import os

from flask import abort, redirect, request

import dashboard
import database as db
from dashboard_routes import (
    _dashboard_csrf_token,
    _require_dashboard_csrf,
    _require_dashboard_session,
)


def install(web, logins, active_account):
    """Keep source authorization and destination login on product permissions."""
    if os.environ.get("OPENLEG_SANDBOX_WORKER") != "1":
        raise RuntimeError("archive exercise requires a village worker")

    @web.get("/sandbox/archive/context")
    def archive_context():
        """List communities the current member may administer for an archive exercise."""
        actor = _require_dashboard_session()
        communities = [
            row
            for row in db.fetch_user_communities(actor) or []
            if dashboard._require_confirmed_admin(row["community_id"], actor)
        ]
        if not communities:
            abort(403)
        return {
            "actor": actor,
            "communities": [
                {"community_id": row["community_id"], "name": row["name"]}
                for row in communities
            ],
            "csrf_token": _dashboard_csrf_token(),
            "account": active_account()[1],
        }

    @web.post("/sandbox/archive/authorize")
    def archive_authorize():
        """Recheck source administrator authority and CSRF before archive actions."""
        actor = _require_dashboard_session()
        _require_dashboard_csrf()
        community_id = request.form.get("community_id", "")
        if not dashboard._require_confirmed_admin(community_id, actor):
            abort(403)
        return {"actor": actor, "community_id": community_id}

    @web.get("/sandbox/archive/enter")
    def archive_enter():
        """Sign in a verified restored administrator using gateway-supplied identity."""
        actor = request.headers.get("X-OpenLEG-Restore-Actor")
        community_id = request.headers.get("X-OpenLEG-Restore-Community")
        profile = db.get_building(actor) if actor else None
        if (
            not os.environ.get("OPENLEG_SANDBOX_RESTORE_SOURCE")
            or not profile
            or not profile.get("verified")
            or not dashboard._require_confirmed_admin(community_id, actor)
        ):
            abort(403)
        logins.sign_in("dashboard_building_id", actor)
        return redirect("/leg/dashboard?cid=" + community_id)

    @web.after_request
    def label_archive(response):
        """Give successful community exports an explicit simulation filename."""
        if (request.endpoint or "").rsplit(".", 1)[
            -1
        ] == "leg_community_archive_export" and response.status_code == 200:
            response.headers.set(
                "Content-Disposition",
                "attachment",
                filename="simulation-sonnwil-community.json",
            )
        return response
