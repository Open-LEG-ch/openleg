# SPDX-License-Identifier: AGPL-3.0-or-later
"""A single village worker, with no internet sockets or provisioning credentials."""

import os
import secrets
from pathlib import Path

from sandbox_network import deny_internet


def create_worker():
    if os.environ.get("OPENLEG_SANDBOX_WORKER") != "1":
        raise RuntimeError("sandbox worker must be explicitly enabled")
    directory = Path(os.environ["OPENLEG_SANDBOX_DIRECTORY"]).resolve()
    if directory != Path.cwd().resolve() or not os.environ.get(
        "OPENLEG_SANDBOX_WORKSPACE"
    ):
        raise RuntimeError("sandbox workspace is not configured")
    deny_internet()

    from flask import redirect, render_template, session
    from werkzeug.middleware.proxy_fix import ProxyFix

    import database as db
    from app import create_app
    from dashboard_routes import _dashboard_session_building_id, _set_dashboard_session

    if not db.init_db():
        raise RuntimeError("sandbox PostgreSQL required")
    from sandbox_seed import seed_village

    seed_village(db, directory)
    web = create_app(
        {
            "SESSION_COOKIE_NAME": "openleg_product",
            "RATELIMIT_STORAGE_URI": "memory://",
            "MAX_CONTENT_LENGTH": int(os.environ["OPENLEG_SANDBOX_MAX_UPLOAD_BYTES"]),
        },
        load_environment=False,
    )
    # Only the gateway can reach this private Unix listener. It strips browser
    # proxy headers and supplies the configured public scheme itself.
    web.wsgi_app = ProxyFix(
        web.wsgi_app, x_for=0, x_proto=1, x_host=0, x_port=0, x_prefix=0
    )

    @web.context_processor
    def simulation():
        building_id = _dashboard_session_building_id()
        return {
            "sandbox_mode": True,
            "private_page": True,
            "sandbox_household": db.get_building(building_id) if building_id else None,
        }

    @web.get("/sandbox/enter")
    def enter():
        _set_dashboard_session("sonnwil-resident")
        return redirect("/dashboard")

    @web.get("/sandbox/home")
    def home():
        session.setdefault("sandbox_csrf", secrets.token_urlsafe(32))
        return render_template(
            "sandbox/home.html",
            csrf_token=session["sandbox_csrf"],
        )

    @web.get("/sandbox/inbox")
    def inbox():
        from sandbox_mail import messages

        return render_template("sandbox/inbox.html", messages=messages())

    @web.after_request
    def label_simulation(response):
        response.headers["X-OpenLEG-Simulation"] = "Sonnwil"
        return response

    return web


application = create_worker()
