# SPDX-License-Identifier: AGPL-3.0-or-later
"""Header-authenticated advisory review and private owner in-app notices."""

import hmac
import json
from datetime import UTC, datetime

from flask import Blueprint, abort, jsonify, redirect, request, session, url_for

import advisories
import database as db
from admin import require_admin
from advisories import MAX_BYTES, AdvisoryError

advisory_bp = Blueprint("advisories", __name__)


@advisory_bp.errorhandler(AdvisoryError)
def _error(exc):
    if request.path.startswith("/dashboard/"):
        from app import render_city_template

        return render_city_template("advisories_error.html"), exc.status
    return jsonify(error=exc.code, field=exc.field), exc.status


@advisory_bp.before_request
def _bound_mutation():
    if request.method in ("POST", "PUT"):
        request.max_content_length = MAX_BYTES + 1
        if request.content_encoding or (request.content_length or 0) > MAX_BYTES:
            raise AdvisoryError("payload_too_large", 413)
        if (
            request.mimetype == "application/x-www-form-urlencoded"
            and len(request.get_data(cache=True)) > MAX_BYTES
        ):
            raise AdvisoryError("payload_too_large", 413)


def _admin():
    # require_admin's older endpoints accept query/form tokens. This catalog
    # admits only the explicit header, including on reads of reviewer identities.
    if not request.headers.get("X-Admin-Token") or "token" in request.args:
        abort(403)
    require_admin()


def _payload():
    if request.mimetype != "application/json":
        raise AdvisoryError("json_required", 415)
    raw = request.stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise AdvisoryError("payload_too_large", 413)
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise AdvisoryError() from None


@advisory_bp.route("/api/admin/advisories/v1/catalog", methods=["POST", "GET"])
def catalog():
    _admin()
    if request.method == "POST":
        return jsonify(db.create_advisory(_payload())), 201
    return jsonify(entries=db.list_advisory_catalog())


@advisory_bp.get("/api/admin/advisories/v1/catalog/<uuid:advisory_id>")
def history(advisory_id):
    _admin()
    return jsonify(revisions=db.get_advisory_history(str(advisory_id)))


@advisory_bp.post("/api/admin/advisories/v1/catalog/<uuid:advisory_id>/revisions")
def revise(advisory_id):
    _admin()
    return jsonify(db.append_advisory_revision(str(advisory_id), _payload())), 201


@advisory_bp.post(
    "/api/admin/advisories/v1/catalog/<uuid:advisory_id>/revisions/<int:revision>/publish"
)
def publish(advisory_id, revision):
    _admin()
    return jsonify(db.publish_advisory_revision(str(advisory_id), revision, _payload()))


@advisory_bp.route("/api/admin/advisories/v1/source-policy", methods=["GET", "POST"])
def source_policy():
    _admin()
    if request.method == "POST":
        return jsonify(db.set_advisory_source_policy(_payload())), 201
    return jsonify(db.get_advisory_source_policy())


@advisory_bp.route("/api/admin/advisories/v1/source-reviews", methods=["GET", "POST"])
def source_reviews():
    _admin()
    if request.method == "POST":
        return jsonify(db.record_advisory_source_review(_payload())), 201
    return jsonify(reviews=db.list_advisory_source_reviews())


def _actor(mutation=False):
    actor = session.get("dashboard_building_id")
    if not isinstance(actor, str) or not actor.strip():
        raise AdvisoryError("session_required", 401)
    if mutation:
        submitted = request.headers.get("X-CSRF-Token", "")
        if (
            request.path.startswith("/dashboard/")
            and request.mimetype == "application/x-www-form-urlencoded"
        ):
            submitted = request.form.get("csrf_token", "")
        expected = session.get("dashboard_csrf_token")
        if (
            not isinstance(expected, str)
            or not expected
            or not submitted
            or not hmac.compare_digest(submitted.encode(), expected.encode())
        ):
            raise AdvisoryError("csrf_failed", 403)
    return actor


@advisory_bp.get("/api/equipment/v1/records/<uuid:equipment_id>/advisories")
def owner_advisories(equipment_id):
    return jsonify(db.get_equipment_advisories(_actor(), str(equipment_id)))


@advisory_bp.post(
    "/api/equipment/v1/records/<uuid:equipment_id>/advisories/<uuid:advisory_id>/<int:revision>/actions"
)
def owner_action(equipment_id, advisory_id, revision):
    return jsonify(
        db.record_equipment_advisory_action(
            _actor(mutation=True),
            str(equipment_id),
            str(advisory_id),
            revision,
            _payload(),
        )
    )


def _render_owner(view, **extra):
    from app import render_city_template

    return render_city_template(
        "advisories.html",
        **view,
        advisory=advisories,
        csrf_token=session.get("dashboard_csrf_token", ""),
        today=datetime.now(UTC).date().isoformat(),
        **extra,
    )


@advisory_bp.get("/dashboard/equipment/<uuid:equipment_id>/advisories")
def owner_page(equipment_id):
    return _render_owner(db.get_equipment_advisories(_actor(), str(equipment_id)))


def _form_action():
    return {
        "context_digest": request.form.get("context_digest"),
        "acknowledged": request.form.get("acknowledged") == "yes",
        "remedy_status": request.form.get("remedy_status", "unknown"),
        "evidence": {
            "kind": request.form.get("evidence.kind", "unknown"),
            "reviewed_on": request.form.get("evidence.reviewed_on") or None,
            "source_url": request.form.get("evidence.source_url") or None,
        },
        "private_note": request.form.get("private_note") or None,
    }


@advisory_bp.post(
    "/dashboard/equipment/<uuid:equipment_id>/advisories/<uuid:advisory_id>/<int:revision>/actions"
)
def owner_form_action(equipment_id, advisory_id, revision):
    actor = _actor(mutation=True)
    if request.mimetype != "application/x-www-form-urlencoded":
        raise AdvisoryError("form_required", 415)
    payload = _form_action()
    try:
        db.record_equipment_advisory_action(
            actor, str(equipment_id), str(advisory_id), revision, payload
        )
    except AdvisoryError as exc:
        if exc.status != 400:
            raise
        view = db.get_equipment_advisories(actor, str(equipment_id))
        return _render_owner(
            view,
            invalid_action=True,
            submitted_action=payload,
            submitted_id=str(advisory_id),
            submitted_revision=revision,
        ), 400
    return redirect(url_for("advisories.owner_page", equipment_id=equipment_id))
