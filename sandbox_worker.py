# SPDX-License-Identifier: AGPL-3.0-or-later
"""A single village worker, with no internet sockets or provisioning credentials."""

import hmac
import os
import secrets
from pathlib import Path

from sandbox_network import deny_internet


def create_worker():
    """Build the confined product app and install private village exercises."""
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
    from translation_markers import gettext_noop as _N

    seed_identities = seed_village(db, directory)
    web = create_app(
        {
            "SESSION_COOKIE_NAME": "openleg_product",
            "SANDBOX_SIMULATION": True,
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
    from sandbox_metering import install as install_metering

    install_metering(web)
    from sandbox_billing import install as install_billing

    install_billing(web)
    from sandbox_payments import install as install_payments

    install_payments(web)
    from sandbox_questions import install as install_questions

    install_questions(web)
    from sandbox_telemetry import install as install_telemetry

    install_telemetry(web)

    def seeded_household(profile):
        """A reused building ID cannot adopt the original demo registration."""
        proof = seed_identities.get(profile["building_id"]) if profile else None
        return bool(
            proof
            and profile.get("verified")
            and hmac.compare_digest(proof, profile.get("referral_code") or "")
        )

    def active_account():
        building = _dashboard_session_building_id()
        profile = db.get_building(building) if building else None
        if profile and profile.get("verified"):
            for key, account in ACCOUNTS.items():
                if account.get("building_id") == building and seeded_household(profile):
                    return key, account
            return None, {
                "name": profile.get("address") or _N("Neuer Haushalt"),
                "role": _N("Haushalt"),
                "scope": "Sonnwil",
                "building_id": building,
                "email": profile["email"],
                "destination": "/dashboard",
                "scenario": _N("Energieprofil ändern"),
                "starting_state": _N("Ihr neuer Haushalt ist bestätigt."),
                "instructions": _N(
                    "Ändern Sie Verbrauch oder PV-Leistung im Energieprofil."
                ),
                "expected": _N(
                    "Die gespeicherten Angaben erscheinen nach dem Neuladen."
                ),
            }
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
            profile = db.get_building(account["building_id"])
            if not seeded_household(profile):
                abort(
                    web.make_response(
                        (render_template("sandbox/account_unavailable.html"), 410)
                    )
                )
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
            accounts={
                key: account
                for key, account in ACCOUNTS.items()
                if not account.get("building_id")
                or seeded_household(db.get_building(account["building_id"]))
            },
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

        return render_template(
            "sandbox/inbox.html",
            messages=messages(viewer=_dashboard_session_building_id()),
        )

    @web.get("/sandbox/onboarding")
    def onboarding():
        return render_template(
            "sandbox/onboarding.html", building_id="sonnwil-new-" + secrets.token_hex(8)
        )

    @web.after_request
    def label_simulation(response):
        response.headers["X-OpenLEG-Simulation"] = "Sonnwil"
        if request.path == "/dashboard/export" and response.status_code == 200:
            response.direct_passthrough = False
            payload = response.get_json()
            payload["_simulation"] = {"village": "Sonnwil", "synthetic": True}
            response.set_data(web.json.dumps(payload))
            response.headers.set(
                "Content-Disposition",
                "attachment",
                filename="simulation-sonnwil-profil.json",
            )
        return response

    from sandbox_formation import install

    install(web)
    return web


application = create_worker()
