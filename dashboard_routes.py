# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dashboard and LEG operator route handlers.

Routes are registered onto a caller-provided blueprint by
``register_dashboard_routes`` so dependencies such as the rate limiter and the
city-template renderer are resolved at app-creation time. This avoids stale
closures across test reloads.
"""

import hmac
import io
import json
import secrets

from flask import (
    abort,
    current_app,
    g,
    make_response,
    redirect,
    request,
    send_file,
    session,
)

import dashboard as dashboard_module
import dashboard_access as dashboard_access_module
import database as db
import security_utils


def _dashboard_session_building_id():
    return (session.get("dashboard_building_id") or "").strip()


def _dashboard_csrf_token():
    token = session.get("dashboard_csrf_token") or ""
    return token if isinstance(token, str) else ""


def _set_dashboard_session(building_id: str):
    session.clear()
    session.permanent = True
    session["dashboard_building_id"] = building_id
    session["dashboard_csrf_token"] = secrets.token_urlsafe(32)


def _exchange_response(location: str):
    response = redirect(location)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _mark_private_response(response):
    response = make_response(response)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _require_dashboard_session():
    building_id = _dashboard_session_building_id()
    if not building_id:
        abort(401)
    return building_id


def _require_dashboard_csrf():
    submitted = request.form.get("csrf_token", "")
    if not isinstance(submitted, str) or not submitted.isascii():
        abort(400)
    expected = _dashboard_csrf_token()
    if not expected or not hmac.compare_digest(submitted, expected):
        abort(400)


def _dashboard_context(building_id: str, *, read_only: bool = False, **extra):
    city_id = g.tenant.get("territory") if hasattr(g, "tenant") else None
    context = dashboard_module.readiness(
        building_id,
        city_id=city_id,
        app_base_url=current_app.config["APP_BASE_URL"],
    )
    context.update(extra)
    context.setdefault("read_only", read_only)
    context.setdefault("access_request_sent", False)
    context.setdefault("csrf_token", _dashboard_csrf_token())
    return context


def _dashboard_public_context(**extra):
    context = {
        "error": None,
        "user": None,
        "readiness_score": 0,
        "checks": [],
        "neighbor_count": 0,
        "referral_link": "",
        "read_only": False,
        "access_request_sent": False,
    }
    context.update(extra)
    return context


def _leg_dashboard_redirect(community_id):
    return redirect(dashboard_module.leg_dashboard_location(community_id))


def _rate_limit(limiter, limit_string):
    """Apply Flask-Limiter rules using the limiter passed at registration time."""
    if limiter is not None:
        return limiter.limit(limit_string)
    return lambda f: f


def register_dashboard_routes(bp, *, send_email, limiter, render_city_template):
    """Register all resident and LEG dashboard routes on *bp*."""

    @bp.route("/dashboard")
    def dashboard():
        session_building_id = _dashboard_session_building_id()
        if session_building_id:
            return _mark_private_response(
                render_city_template(
                    "dashboard.html",
                    **_dashboard_context(
                        session_building_id,
                        profile_saved=request.args.get("saved") == "1",
                    ),
                )
            )
        return render_city_template("dashboard.html", **_dashboard_public_context())

    @bp.route("/dashboard/profile", methods=["POST"])
    def dashboard_profile_update():
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        potential_pv_kwp = request.form.get("potential_pv_kwp")
        if request.form.get("has_solar") in {"no", "planned"}:
            potential_pv_kwp = ""
        result = dashboard_module.update_profile(
            building_id,
            annual_consumption_kwh=request.form.get("annual_consumption_kwh"),
            potential_pv_kwp=potential_pv_kwp,
            share_with_utility="share_with_utility" in request.form,
            share_with_neighbors="share_with_neighbors" in request.form,
        )
        if result["error"]:
            response = render_city_template(
                "dashboard.html",
                **_dashboard_context(
                    building_id,
                    profile_error=result["error"],
                ),
            )
            return _mark_private_response(response), 400
        return _exchange_response("/dashboard?saved=1")

    @bp.route("/dashboard/export")
    def dashboard_profile_export():
        building_id = _require_dashboard_session()
        payload = json.dumps(
            dashboard_module.export_profile(building_id),
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        return _mark_private_response(
            send_file(
                io.BytesIO(payload),
                mimetype="application/json",
                as_attachment=True,
                download_name="openleg-profil.json",
            )
        )

    @bp.route("/dashboard/access/<token>")
    @_rate_limit(limiter, "10 per minute")
    def dashboard_access_exchange(token):
        building_id = dashboard_access_module.consume_access_token(db, token)
        if not building_id:
            return _exchange_response("/dashboard?access=invalid")
        _set_dashboard_session(building_id)
        return _exchange_response("/dashboard")

    @bp.route("/dashboard/access/request", methods=["POST"])
    @_rate_limit(limiter, "5 per minute")
    def dashboard_access_request():
        email = (request.form.get("email") or "").strip().lower()
        generic_message = (
            "Falls ein Profil zu dieser E-Mail-Adresse existiert, haben wir einen "
            "neuen Zugangslink gesendet."
        )
        (
            is_valid_email,
            normalized_email,
            _error,
        ) = security_utils.validate_email_address(email)
        if not is_valid_email or not normalized_email:
            return render_city_template(
                "dashboard.html",
                **_dashboard_public_context(
                    access_request_sent=True,
                    access_request_message=generic_message,
                ),
            )

        for profile in db.get_building_by_email(normalized_email) or []:
            building_id = (profile.get("building_id") or "").strip()
            if not building_id:
                continue
            token = dashboard_access_module.issue_access_token(
                db,
                building_id,
                ttl_seconds=current_app.config["DASHBOARD_ACCESS_TOKEN_TTL_SECONDS"],
            )
            if not token:
                continue
            url = dashboard_access_module.access_url(
                current_app.config["APP_BASE_URL"].rstrip("/") + "/", token
            )
            try:
                send_email(
                    normalized_email,
                    "Ihr Dashboard-Zugangslink",
                    "Öffnen Sie Ihr Dashboard über diesen Link:\n\n"
                    f"{url}\n\n"
                    "Falls Sie diesen Link nicht angefordert haben, können Sie diese "
                    "E-Mail ignorieren.",
                )
            except Exception:
                current_app.logger.exception("Failed to send dashboard access email")
        return render_city_template(
            "dashboard.html",
            **_dashboard_public_context(
                access_request_sent=True,
                access_request_message=generic_message,
            ),
        )

    @bp.route("/dashboard/logout", methods=["POST"])
    def dashboard_logout():
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        db.revoke_dashboard_access_tokens(building_id)
        session.clear()
        return redirect("/dashboard")

    @bp.route("/dashboard/demo")
    def dashboard_demo():
        return render_city_template(
            "dashboard.html", **dashboard_module.demo_readiness()
        )

    @bp.route("/leg/dashboard")
    def leg_dashboard():
        community_id = request.args.get("cid", "").strip()
        session_building_id = _dashboard_session_building_id()
        response = render_city_template(
            "leg_dashboard.html",
            **dashboard_module.leg_overview(community_id, session_building_id),
            viewer_has_session=bool(session_building_id),
            csrf_token=_dashboard_csrf_token(),
        )
        if session_building_id:
            return _mark_private_response(response)
        return response

    @bp.route("/leg/dashboard/demo")
    def leg_dashboard_demo():
        return render_city_template(
            "leg_dashboard.html",
            **dashboard_module.leg_demo_overview(),
            viewer_has_session=False,
            csrf_token="",
        )

    @bp.route("/leg/community/create", methods=["POST"])
    def leg_community_create():
        name = request.form.get("name", "")
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        model = request.form.get("distribution_model", "simple")
        result = dashboard_module.leg_create(name, building_id, model)
        if result["error"]:
            return (
                render_city_template(
                    "leg_dashboard.html", error=result["error"], community=None
                ),
                400,
            )
        return _leg_dashboard_redirect(result["community_id"])

    @bp.route("/leg/community/<community_id>/invite", methods=["POST"])
    def leg_community_invite(community_id):
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        invite_email = request.form.get("invite_email", "").strip()
        dashboard_module.leg_invite_by_email(community_id, building_id, invite_email)
        return _leg_dashboard_redirect(community_id)

    @bp.route("/leg/community/<community_id>/confirm", methods=["POST"])
    def leg_community_confirm(community_id):
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        dashboard_module.leg_confirm(community_id, building_id)
        return _leg_dashboard_redirect(community_id)

    @bp.route("/leg/community/<community_id>/start-formation", methods=["POST"])
    def leg_community_start_formation(community_id):
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        dashboard_module.leg_start_formation(community_id, building_id)
        return _leg_dashboard_redirect(community_id)

    @bp.route("/leg/community/<community_id>/documents", methods=["POST"])
    def leg_community_documents(community_id):
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        dashboard_module.leg_generate_documents(community_id, building_id)
        return _leg_dashboard_redirect(community_id)

    @bp.route("/leg/community/<community_id>/correspondence", methods=["POST"])
    def leg_community_correspondence(community_id):
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        dashboard_module.leg_log_correspondence(
            community_id,
            building_id,
            direction=request.form.get("direction", ""),
            channel=request.form.get("channel", ""),
            counterparty=request.form.get("counterparty", ""),
            subject=request.form.get("subject", ""),
            notes=request.form.get("notes", ""),
        )
        return _leg_dashboard_redirect(community_id)

    @bp.route("/leg/document/<int:doc_id>")
    def leg_document_download(doc_id):
        building_id = _dashboard_session_building_id()
        if not building_id:
            abort(404)
        doc = dashboard_module.leg_document_for_member(doc_id, building_id)
        if not doc:
            abort(404)
        response = _mark_private_response(
            send_file(
                io.BytesIO(bytes(doc["pdf_data"])),
                mimetype="application/pdf",
                as_attachment=False,
                download_name=doc["filename"],
            )
        )
        return response
