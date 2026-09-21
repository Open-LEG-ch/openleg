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
    redirect,
    request,
    send_file,
    session,
    url_for,
)

import access_token
import billing_policy
import dashboard as dashboard_module
import database as db
import payment_reconciliation
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


def _reject_constant(constant: str) -> float:
    raise ValueError(f"Non-standard JSON constant: {constant}")


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
        "neighbor_box_half_width_m": int(db.NEIGHBOR_BOX_HALF_WIDTH_KM * 1000),
        "referral_link": "",
        "read_only": False,
        "access_request_sent": False,
    }
    context.update(extra)
    return context


def _leg_dashboard_redirect(community_id, saved=None):
    location = dashboard_module.leg_dashboard_location(community_id)
    if saved:
        location = f"{location}?saved={saved}"
    return redirect(location)


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
            return render_city_template(
                "dashboard.html",
                **_dashboard_context(
                    session_building_id,
                    profile_saved=request.args.get("saved") == "1",
                ),
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
            return (
                render_city_template(
                    "dashboard.html",
                    **_dashboard_context(
                        building_id,
                        profile_error=result["error"],
                    ),
                ),
                400,
            )
        return redirect("/dashboard?saved=1")

    @bp.route("/dashboard/export")
    def dashboard_profile_export():
        building_id = _require_dashboard_session()
        payload = json.dumps(
            dashboard_module.export_profile(building_id),
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        return send_file(
            io.BytesIO(payload),
            mimetype="application/json",
            as_attachment=True,
            download_name="openleg-profil.json",
        )

    @bp.route("/dashboard/access/<token>")
    @_rate_limit(limiter, "10 per minute")
    def dashboard_access_exchange(token):
        building_id = access_token.consume(access_token.DASHBOARD, db, token)
        if not building_id:
            return redirect("/dashboard?access=invalid")
        _set_dashboard_session(building_id)
        return redirect("/dashboard")

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
            token = access_token.issue(
                access_token.DASHBOARD,
                db,
                building_id,
                ttl_seconds=current_app.config["DASHBOARD_ACCESS_TOKEN_TTL_SECONDS"],
            )
            if not token:
                continue
            url = access_token.access_url(
                access_token.DASHBOARD, current_app.config["APP_BASE_URL"], token
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

    @bp.route("/dashboard/invoices")
    def dashboard_invoices():
        building_id = _require_dashboard_session()
        try:
            view = dashboard_module.member_invoices_view(building_id)
        except (db.BillingStoreError, dashboard_module.MemberInvoiceDataError):
            abort(503)
        return render_city_template("member_invoices.html", **view)

    @bp.route("/dashboard/invoices/<int:invoice_id>")
    def dashboard_invoice_detail(invoice_id):
        building_id = _require_dashboard_session()
        try:
            invoice = dashboard_module.member_invoice_detail(invoice_id, building_id)
        except (db.BillingStoreError, dashboard_module.MemberInvoiceDataError):
            abort(503)
        if not invoice:
            abort(404)
        try:
            savings = dashboard_module.member_invoice_savings_view(
                invoice_id, building_id
            )
        except dashboard_module.MemberSavingsDataError:
            abort(503)
        return render_city_template(
            "member_invoice_detail.html",
            invoice=invoice,
            savings=savings,
            csrf_token=_dashboard_csrf_token(),
        )

    @bp.route("/dashboard/invoices/<int:invoice_id>/queries", methods=["POST"])
    def dashboard_invoice_query_open(invoice_id):
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        upload = request.files.get("attachment")
        filename = ""
        data = None
        if upload and upload.filename:
            data = upload.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024 or upload.mimetype != "application/pdf":
                abort(400)
            filename = upload.filename
        result = dashboard_module.member_open_invoice_query(
            invoice_id,
            building_id,
            request.form.get("category", ""),
            request.form.get("message", ""),
            filename,
            data,
        )
        if result["error"]:
            abort(400)
        return redirect(
            url_for(".dashboard_invoice_detail", invoice_id=invoice_id) + "#questions"
        )

    @bp.route(
        "/dashboard/invoices/<int:invoice_id>/queries/<int:query_id>/reply",
        methods=["POST"],
    )
    def dashboard_invoice_query_reply(invoice_id, query_id):
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        result = dashboard_module.member_reply_invoice_query(
            query_id, building_id, request.form.get("message", "")
        )
        if result["error"]:
            abort(400)
        return redirect(
            url_for(".dashboard_invoice_detail", invoice_id=invoice_id) + "#questions"
        )

    @bp.route("/dashboard/invoices/<int:invoice_id>/pdf")
    def dashboard_invoice_pdf(invoice_id):
        building_id = _require_dashboard_session()
        try:
            invoice = dashboard_module.member_invoice_detail(invoice_id, building_id)
        except (db.BillingStoreError, dashboard_module.MemberInvoiceDataError):
            abort(503)
        if not invoice:
            abort(404)
        pdf_bytes = dashboard_module.member_invoice_pdf_bytes(invoice)
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"rechnung-{invoice['invoice_number']}.pdf",
        )

    @bp.route("/leg/dashboard")
    def leg_dashboard():
        community_id = request.args.get("cid", "").strip()
        session_building_id = _dashboard_session_building_id()
        return render_city_template(
            "leg_dashboard.html",
            **dashboard_module.leg_overview(community_id, session_building_id),
            viewer_has_session=bool(session_building_id),
            csrf_token=_dashboard_csrf_token(),
        )

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
        result = dashboard_module.leg_invite_by_email(
            community_id, building_id, invite_email
        )
        if result["error"]:
            return (
                render_city_template(
                    "leg_dashboard.html",
                    **dashboard_module.leg_overview(community_id, building_id),
                    viewer_has_session=True,
                    csrf_token=_dashboard_csrf_token(),
                    invite_error=result["error"],
                ),
                400,
            )
        return _leg_dashboard_redirect(community_id)

    @bp.route(
        "/leg/community/<community_id>/members/<member_building_id>/roles",
        methods=["POST"],
    )
    def leg_community_member_roles(community_id, member_building_id):
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        result = dashboard_module.leg_set_member_roles(
            community_id,
            building_id,
            member_building_id,
            request.form.getlist("roles"),
        )
        if result["error"]:
            return (
                render_city_template(
                    "leg_dashboard.html",
                    **dashboard_module.leg_overview(community_id, building_id),
                    viewer_has_session=True,
                    csrf_token=_dashboard_csrf_token(),
                    roles_error=result["error"],
                ),
                400,
            )
        return _leg_dashboard_redirect(community_id)

    @bp.route("/leg/community/<community_id>/access-policy", methods=["POST"])
    def leg_community_access_policy(community_id):
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        result = dashboard_module.leg_set_dual_control(
            community_id,
            building_id,
            request.form.get("require_dual_control") == "yes",
        )
        if result["error"]:
            abort(403)
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

    @bp.route("/leg/community/<community_id>/archive")
    def leg_community_archive_export(community_id):
        building_id = _require_dashboard_session()
        payload = dashboard_module.leg_export_archive(community_id, building_id)
        if payload is None:
            abort(403)
        return send_file(
            io.BytesIO(payload),
            mimetype="application/json",
            as_attachment=True,
            download_name=f"openleg-{community_id}.json",
        )

    def _restore_archive_response(community_id, *, dry_run):
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        upload = request.files.get("archive")
        if upload is None:
            abort(400)
        payload = upload.read(100 * 1024 * 1024 + 1)
        if len(payload) > 100 * 1024 * 1024:
            abort(413)
        result = dashboard_module.leg_restore_archive(
            community_id, building_id, payload, dry_run=dry_run
        )
        if result is None:
            abort(403)
        return result, (200 if result["valid"] else 400)

    @bp.route("/leg/community/<community_id>/archive/dry-run", methods=["POST"])
    def leg_community_archive_dry_run(community_id):
        return _restore_archive_response(community_id, dry_run=True)

    @bp.route("/leg/community/<community_id>/archive/restore", methods=["POST"])
    def leg_community_archive_restore(community_id):
        return _restore_archive_response(community_id, dry_run=False)

    @bp.route("/leg/community/<community_id>/vnb-submissions", methods=["POST"])
    def leg_community_vnb_submission(community_id):
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        result = dashboard_module.leg_submit_vnb_formation(community_id, building_id)
        if result["error"]:
            abort(result.get("error_status", 409))
        return _leg_dashboard_redirect(community_id)

    @bp.route("/leg/community/<community_id>/vnb-submissions/<case_id>/handover")
    def leg_community_vnb_handover(community_id, case_id):
        building_id = _require_dashboard_session()
        package = dashboard_module.leg_vnb_manual_package(
            community_id, case_id, building_id
        )
        if not package:
            abort(404)
        return send_file(
            io.BytesIO(package["manual_package"]),
            mimetype="application/zip",
            as_attachment=True,
            download_name="openleg-vnb-anmeldung.zip",
        )

    @bp.route(
        "/leg/community/<community_id>/vnb-submissions/<case_id>/delivered",
        methods=["POST"],
    )
    def leg_community_vnb_delivered(community_id, case_id):
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        result = dashboard_module.leg_mark_vnb_manual_delivered(
            community_id, case_id, building_id
        )
        if result["error"]:
            abort(result.get("error_status", 409))
        return _leg_dashboard_redirect(community_id)

    @bp.route("/leg/community/<community_id>/vnb-mutations", methods=["POST"])
    def leg_community_vnb_mutation(community_id):
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        after_facts = {}
        if request.form.get("after_facts", "").strip():
            try:
                after_facts = json.loads(
                    request.form["after_facts"], parse_constant=_reject_constant
                )
            except (json.JSONDecodeError, ValueError):
                abort(400)
            if not isinstance(after_facts, dict):
                abort(400)
        result = dashboard_module.leg_submit_vnb_mutation(
            community_id,
            building_id,
            request.form.get("mutation_id", ""),
            request.form.get("participant_id", ""),
            request.form.get("mutation_type", ""),
            request.form.get("effective_date", ""),
            request.form.get("source_agreement_id", ""),
            after_facts,
        )
        if result["error"]:
            abort(result.get("error_status", 409))
        return _leg_dashboard_redirect(community_id)

    @bp.route("/leg/community/<community_id>/vnb-mutations/<case_id>/handover")
    def leg_community_vnb_mutation_handover(community_id, case_id):
        building_id = _require_dashboard_session()
        package = dashboard_module.leg_vnb_mutation_manual_package(
            community_id, case_id, building_id
        )
        if not package:
            abort(404)
        return send_file(
            io.BytesIO(package["manual_package"]),
            mimetype="application/zip",
            as_attachment=True,
            download_name="openleg-vnb-mitgliedermutation.zip",
        )

    @bp.route(
        "/leg/community/<community_id>/vnb-mutations/<case_id>/delivered",
        methods=["POST"],
    )
    def leg_community_vnb_mutation_delivered(community_id, case_id):
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        result = dashboard_module.leg_mark_vnb_mutation_delivered(
            community_id, case_id, building_id
        )
        if result["error"]:
            abort(result.get("error_status", 409))
        return _leg_dashboard_redirect(community_id)

    @bp.route("/leg/community/<community_id>/billing")
    def leg_billing_workspace(community_id):
        building_id = _require_dashboard_session()
        try:
            view = dashboard_module.leg_billing_workspace_view(
                community_id,
                building_id,
                include_statement_entries=True,
                billing_approved=request.args.get("approved") == "1",
                statement_status=request.args.get("statement"),
            )
        except db.BillingStoreError:
            abort(503)
        if view["error"]:
            abort(403)
        return render_city_template(
            "leg_billing.html",
            csrf_token=_dashboard_csrf_token(),
            **view,
        )

    @bp.route("/leg/community/<community_id>/billing/statements", methods=["POST"])
    def leg_billing_statement_import(community_id):
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        upload = request.files.get("statement")
        if upload is None or not upload.filename:
            abort(400)
        content = upload.stream.read(5 * 1024 * 1024 + 1)
        if len(content) > 5 * 1024 * 1024:
            abort(413)
        try:
            result = dashboard_module.leg_import_bank_statement(
                community_id, building_id, upload.filename, content
            )
        except payment_reconciliation.StatementError:
            abort(400)
        except db.BillingStoreError:
            abort(503)
        if result["error"]:
            abort(403)
        suffix = (
            "?statement=duplicate" if result["duplicate"] else "?statement=imported"
        )
        return redirect(
            dashboard_module.leg_billing_workspace_location(community_id) + suffix
        )

    @bp.route(
        "/leg/community/<community_id>/billing/period/<int:period_id>/prepare",
        methods=["POST"],
    )
    def leg_billing_period_prepare(community_id, period_id):
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        if request.form.get("confirm_prepare") != "yes":
            abort(400)
        result = dashboard_module.leg_prepare_billing_period(
            community_id, building_id, period_id
        )
        if result["error"]:
            abort(result["error_status"])
        return redirect(
            dashboard_module.leg_billing_workspace_location(community_id)
            + "?period=prepared"
        )

    @bp.route(
        "/leg/community/<community_id>/billing/period/<int:period_id>/approve",
        methods=["POST"],
    )
    def leg_billing_period_approve(community_id, period_id):
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        if request.form.get("confirm_approval") != "yes":
            abort(400)
        try:
            result = dashboard_module.leg_approve_billing_period(
                community_id, building_id, period_id
            )
        except db.BillingApprovalError:
            # Domain conflict (invalid, stale, or unreconciled draft): tell the
            # admin without leaking storage or reconciliation internals.
            try:
                view = dashboard_module.leg_billing_workspace_view(
                    community_id, building_id
                )
            except db.BillingStoreError:
                abort(503)
            if view["error"]:
                abort(403)
            view["approval_error"] = (
                "Dieser Abrechnungsentwurf konnte nicht freigegeben werden. "
                "Er ist unvollständig, nicht vollständig abgeglichen oder wurde "
                "zwischenzeitlich verändert. Prüfen Sie die Periode und versuchen "
                "Sie es erneut."
            )
            return (
                render_city_template(
                    "leg_billing.html",
                    csrf_token=_dashboard_csrf_token(),
                    **view,
                ),
                409,
            )
        except db.BillingStoreError:
            abort(503)
        if result["error"]:
            abort(403)
        return redirect(
            dashboard_module.leg_billing_workspace_location(community_id)
            + "?approved=1"
        )

    def _invoice_lifecycle_response(community_id, action):
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        try:
            result = action(building_id)
        except dashboard_module.InvoiceLifecycleError:
            try:
                view = dashboard_module.leg_billing_workspace_view(
                    community_id, building_id
                )
            except db.BillingStoreError:
                abort(503)
            view["lifecycle_error"] = (
                "Die Aktion ist für den aktuellen Rechnungsstatus nicht zulässig."
            )
            return (
                render_city_template(
                    "leg_billing.html",
                    csrf_token=_dashboard_csrf_token(),
                    **view,
                ),
                409,
            )
        except db.BillingStoreError:
            abort(503)
        if result["error"] == "Kein Zugriff.":
            abort(403)
        if result["error"]:
            try:
                view = dashboard_module.leg_billing_workspace_view(
                    community_id, building_id
                )
            except db.BillingStoreError:
                abort(503)
            view["lifecycle_error"] = result["error"]
            return (
                render_city_template(
                    "leg_billing.html",
                    csrf_token=_dashboard_csrf_token(),
                    **view,
                ),
                502,
            )
        return redirect(
            dashboard_module.leg_billing_workspace_location(community_id) + "?updated=1"
        )

    @bp.route(
        "/leg/community/<community_id>/billing/invoice/<int:invoice_id>/deliver",
        methods=["POST"],
    )
    def leg_billing_invoice_deliver(community_id, invoice_id):
        return _invoice_lifecycle_response(
            community_id,
            lambda building_id: dashboard_module.leg_deliver_invoice(
                community_id,
                building_id,
                invoice_id,
                send_email=send_email,
                invoice_url=(
                    current_app.config["APP_BASE_URL"].rstrip("/")
                    + f"/dashboard/invoices/{invoice_id}"
                ),
            ),
        )

    @bp.route(
        "/leg/community/<community_id>/billing/invoice/<int:invoice_id>/delivery-confirmed",
        methods=["POST"],
    )
    def leg_billing_invoice_delivery_confirmed(community_id, invoice_id):
        return _invoice_lifecycle_response(
            community_id,
            lambda building_id: dashboard_module.leg_confirm_invoice_delivery(
                community_id, building_id, invoice_id
            ),
        )

    @bp.route(
        "/leg/community/<community_id>/billing/invoice/<int:invoice_id>/paid",
        methods=["POST"],
    )
    def leg_billing_invoice_paid(community_id, invoice_id):
        return _invoice_lifecycle_response(
            community_id,
            lambda building_id: dashboard_module.leg_record_invoice_payment(
                community_id,
                building_id,
                invoice_id,
                request.form.get("paid_date", ""),
                request.form.get("reference", ""),
            ),
        )

    @bp.route(
        "/leg/community/<community_id>/billing/invoice/<int:invoice_id>/cancel",
        methods=["POST"],
    )
    def leg_billing_invoice_cancel(community_id, invoice_id):
        return _invoice_lifecycle_response(
            community_id,
            lambda building_id: dashboard_module.leg_cancel_invoice(
                community_id,
                building_id,
                invoice_id,
                request.form.get("reason", ""),
            ),
        )

    @bp.route(
        "/leg/community/<community_id>/billing/invoice/<int:invoice_id>/correct",
        methods=["POST"],
    )
    def leg_billing_invoice_correct(community_id, invoice_id):
        return _invoice_lifecycle_response(
            community_id,
            lambda building_id: dashboard_module.leg_correct_invoice(
                community_id,
                building_id,
                invoice_id,
                request.form.get("corrected_invoice_id", ""),
                request.form.get("reason", ""),
            ),
        )

    @bp.route(
        "/leg/community/<community_id>/billing/queries/<int:query_id>",
        methods=["POST"],
    )
    def leg_billing_query_update(community_id, query_id):
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        result = dashboard_module.operator_update_invoice_query(
            community_id,
            building_id,
            query_id,
            message=request.form.get("message", ""),
            status=request.form.get("status", ""),
        )
        if result["error"]:
            abort(400)
        return redirect(dashboard_module.leg_billing_workspace_location(community_id))

    @bp.route("/leg/community/<community_id>/billing-policy")
    def leg_billing_policy_page(community_id):
        building_id = _require_dashboard_session()
        try:
            view = dashboard_module.leg_billing_policy_view(
                community_id,
                building_id,
                policy_saved=request.args.get("saved") == "1",
            )
        except db.BillingStoreError:
            abort(503)
        if view["error"]:
            abort(403)
        return render_city_template(
            "leg_billing_policy.html",
            csrf_token=_dashboard_csrf_token(),
            **view,
        )

    @bp.route("/leg/community/<community_id>/billing-policy", methods=["POST"])
    def leg_billing_policy_save(community_id):
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        try:
            result = dashboard_module.leg_save_billing_policy(
                community_id, building_id, request.form
            )
        except db.BillingStoreError:
            abort(503)
        if result["error"]:
            abort(403)
        if result["errors"]:
            try:
                view = dashboard_module.leg_billing_policy_view(
                    community_id, building_id
                )
            except db.BillingStoreError:
                abort(503)
            view["form_errors"] = result["errors"]
            view["form_values"] = {
                field: request.form.get(field, "")
                for field in billing_policy.FORM_FIELDS
            }
            return (
                render_city_template(
                    "leg_billing_policy.html",
                    csrf_token=_dashboard_csrf_token(),
                    **view,
                ),
                400,
            )
        return redirect(
            dashboard_module.leg_billing_policy_location(community_id) + "?saved=1"
        )

    @bp.route("/leg/community/<community_id>/battery", methods=["POST"])
    def leg_battery_save(community_id):
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        result = dashboard_module.leg_save_battery_asset(
            community_id, building_id, request.form
        )
        if result["error"]:
            abort(403)
        if result["errors"]:
            return (
                render_city_template(
                    "leg_dashboard.html",
                    **dashboard_module.leg_overview(community_id, building_id),
                    viewer_has_session=True,
                    csrf_token=_dashboard_csrf_token(),
                    battery_errors=result["errors"],
                    battery_values={
                        key: request.form.get(key, "")
                        for key in request.form
                        if key != "csrf_token"
                    },
                ),
                400,
            )
        return _leg_dashboard_redirect(community_id, saved="battery")

    @bp.route("/leg/community/<community_id>/correspondence", methods=["POST"])
    def leg_community_correspondence(community_id):
        building_id = _require_dashboard_session()
        _require_dashboard_csrf()
        attachment = request.files.get("attachment")
        attachment_data = (
            attachment.read(2 * 1024 * 1024 + 1)
            if attachment and attachment.filename
            else None
        )
        result = dashboard_module.leg_log_correspondence(
            community_id,
            building_id,
            direction=request.form.get("direction", ""),
            channel=request.form.get("channel", ""),
            counterparty=request.form.get("counterparty", ""),
            subject=request.form.get("subject", ""),
            notes=request.form.get("notes", ""),
            attachment_filename=(
                attachment.filename if attachment and attachment.filename else ""
            ),
            attachment_data=attachment_data,
        )
        if result["error"]:
            return (
                render_city_template(
                    "leg_dashboard.html",
                    **dashboard_module.leg_overview(community_id, building_id),
                    viewer_has_session=True,
                    csrf_token=_dashboard_csrf_token(),
                    correspondence_error=result["error"],
                ),
                400,
            )
        return _leg_dashboard_redirect(community_id)

    @bp.route("/leg/community/<community_id>/correspondence/<int:entry_id>/attachment")
    def leg_correspondence_attachment(community_id, entry_id):
        building_id = _dashboard_session_building_id()
        if not building_id:
            abort(404)
        attachment = dashboard_module.leg_correspondence_attachment(
            entry_id, community_id, building_id
        )
        if not attachment:
            abort(404)
        return send_file(
            io.BytesIO(bytes(attachment["attachment_data"])),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=attachment["attachment_filename"],
        )

    @bp.route("/leg/document/<int:doc_id>")
    def leg_document_download(doc_id):
        building_id = _dashboard_session_building_id()
        if not building_id:
            abort(404)
        doc = dashboard_module.leg_document_for_member(doc_id, building_id)
        if not doc:
            abort(404)
        return send_file(
            io.BytesIO(bytes(doc["pdf_data"])),
            mimetype="application/pdf",
            as_attachment=False,
            download_name=doc["filename"],
        )
