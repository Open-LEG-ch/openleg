# SPDX-License-Identifier: AGPL-3.0-or-later
"""A single village worker, with no internet sockets or provisioning credentials."""

import hmac
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

    from flask import abort, redirect, render_template, request, session
    from werkzeug.middleware.proxy_fix import ProxyFix

    import database as db
    from app import create_app
    from dashboard_routes import _dashboard_session_building_id
    from sandbox_sessions import VillageSessions

    if not db.init_db():
        raise RuntimeError("sandbox PostgreSQL required")
    from sandbox_seed import ACCOUNTS, seed_village

    seed_village(db, directory)
    web = create_app(
        {
            "SESSION_COOKIE_NAME": "openleg_product",
            "SESSION_COOKIE_PATH": os.environ.get("OPENLEG_SANDBOX_PREFIX", "/"),
            "RATELIMIT_STORAGE_URI": "memory://",
            "MAX_CONTENT_LENGTH": int(os.environ["OPENLEG_SANDBOX_MAX_UPLOAD_BYTES"]),
        },
        load_environment=False,
    )
    # Only the gateway can reach this private Unix listener. It strips browser
    # proxy headers and supplies the configured public scheme itself.
    web.wsgi_app = ProxyFix(
        web.wsgi_app, x_for=0, x_proto=1, x_host=0, x_port=0, x_prefix=1
    )

    logins = VillageSessions(directory)
    logins.install(web)

    def active_account():
        building = _dashboard_session_building_id()
        for key, account in ACCOUNTS.items():
            if building and account.get("building_id") == building:
                return key, account
        if session.get("utility_client_id") == "sonnwil-utility":
            return "utility", ACCOUNTS["utility"]
        if session.get("municipality_id"):
            municipality = db.get_municipality(bfs_number=999999)
            if municipality and session["municipality_id"] == municipality["id"]:
                return "municipality", ACCOUNTS["municipality"]
        return None, None

    def sign_in(key):
        account = ACCOUNTS[key]
        if account.get("building_id"):
            logins.sign_in("dashboard_building_id", account["building_id"])
        elif key == "utility":
            logins.sign_in("utility_client_id", "sonnwil-utility")
        else:
            logins.sign_in(
                "municipality_id", db.get_municipality(bfs_number=999999)["id"]
            )
        return account["destination"]

    @web.context_processor
    def simulation():
        _key, account = active_account()
        return {
            "sandbox_mode": True,
            "private_page": True,
            "sandbox_account": account,
            "sandbox_account_key": _key,
        }

    @web.get("/sandbox/enter")
    def enter():
        key = request.headers.get("X-OpenLEG-Initial-Persona")
        if key in ACCOUNTS:
            return redirect(sign_in(key))
        return redirect("/sandbox/home")

    @web.get("/sandbox/accounts")
    def accounts():
        session.setdefault("sandbox_csrf", secrets.token_urlsafe(32))
        return render_template(
            "sandbox/accounts.html",
            accounts=ACCOUNTS,
            csrf_token=session["sandbox_csrf"],
        )

    @web.post("/sandbox/persona")
    def change_persona():
        expected = session.get("sandbox_csrf", "")
        supplied = request.form.get("csrf_token", "")
        if not expected or not hmac.compare_digest(
            expected.encode("utf-8"), supplied.encode("utf-8")
        ):
            abort(400)
        key = request.form.get("persona")
        if key not in ACCOUNTS:
            abort(400)
        return redirect(sign_in(key))

    @web.get("/sandbox/home")
    def home():
        return render_template("sandbox/home.html")

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
