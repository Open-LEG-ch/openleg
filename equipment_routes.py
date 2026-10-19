# SPDX-License-Identifier: AGPL-3.0-or-later
"""Private owner equipment inventory and installer preparation."""

import hmac
import json
from datetime import UTC, datetime

from flask import Blueprint, jsonify, redirect, request, session, url_for

import database as db
import equipment
from equipment import MAX_BYTES, EquipmentError, installer_handoff
from i18n import gettext as _

equipment_bp = Blueprint("equipment", __name__)


@equipment_bp.errorhandler(EquipmentError)
def _error(exc):
    if request.path.startswith("/dashboard/"):
        return _render("equipment_error.html", error_code=exc.code), exc.status
    return jsonify({"error": exc.code}), exc.status


@equipment_bp.before_request
def _bound_mutation():
    if request.method in ("POST", "PUT"):
        # Read one extra byte so terminated streams cannot be silently truncated.
        request.max_content_length = MAX_BYTES + 1
    if request.method in ("POST", "PUT") and (
        request.content_encoding or (request.content_length or 0) > MAX_BYTES
    ):
        raise EquipmentError("payload_too_large", 413)
    if (
        request.method in ("POST", "PUT")
        and request.mimetype == "application/x-www-form-urlencoded"
        and len(request.get_data(cache=True)) > MAX_BYTES
    ):
        raise EquipmentError("payload_too_large", 413)


def _actor(mutation=False):
    actor = session.get("dashboard_building_id")
    if not isinstance(actor, str) or not actor.strip():
        raise EquipmentError("session_required", 401)
    if mutation:
        token = request.headers.get("X-CSRF-Token", "")
        if (
            request.path.startswith("/dashboard/")
            and request.mimetype == "application/x-www-form-urlencoded"
        ):
            token = request.form.get("csrf_token", "")
        expected = session.get("dashboard_csrf_token")
        if (
            not isinstance(expected, str)
            or not expected
            or not token
            or not hmac.compare_digest(token.encode(), expected.encode())
        ):
            raise EquipmentError("csrf_failed", 403)
    return actor


def _payload():
    if request.mimetype != "application/json":
        raise EquipmentError("json_required", 415)
    if request.content_encoding or (request.content_length or 0) > MAX_BYTES:
        raise EquipmentError("payload_too_large", 413)
    raw = request.stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise EquipmentError("payload_too_large", 413)
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise EquipmentError() from None


@equipment_bp.route("/api/equipment/v1/records", methods=["POST", "GET"])
def records():
    actor = _actor(mutation=request.method == "POST")
    if request.method == "POST":
        return jsonify(record=db.create_equipment(actor, _payload())), 201
    return jsonify(records=db.list_equipment(actor))


@equipment_bp.route(
    "/api/equipment/v1/records/<uuid:record_id>", methods=["GET", "PUT", "DELETE"]
)
def record(record_id):
    actor = _actor(mutation=request.method != "GET")
    record_id = str(record_id)
    if request.method == "PUT":
        return jsonify(record=db.update_equipment(actor, record_id, _payload()))
    if request.method == "DELETE":
        return jsonify(db.delete_equipment(actor, record_id))
    return jsonify(record=db.get_equipment(actor, record_id))


@equipment_bp.post("/api/equipment/v1/records/<uuid:record_id>/handoff")
@equipment_bp.post("/dashboard/equipment/<uuid:record_id>/handoff")
def handoff(record_id):
    record = db.get_equipment(_actor(mutation=True), str(record_id))
    export = installer_handoff(record)
    notice = export["notice"]
    export["notice"] = _(notice)
    for collection in (export["findings"], export["sources"]):
        for item in collection:
            label = item["label"]
            item["label"] = _(label)
    response = jsonify(export)
    response.headers["Content-Disposition"] = (
        "attachment; filename=equipment-handoff.json"
    )
    return response


def _render(template, **context):
    from app import render_city_template

    return render_city_template(
        template,
        sources=equipment.SOURCE_LINKS,
        csrf_token=session.get("dashboard_csrf_token", ""),
        today=datetime.now(UTC).date().isoformat(),
        equipment=equipment,
        **context,
    )


def _form_evidence(prefix):
    return {
        key: request.form.get(prefix + "." + key) or default
        for key, default in (
            ("kind", "unknown"),
            ("reviewed_on", None),
            ("source_url", None),
        )
    }


def _form_payload():
    if request.mimetype != "application/x-www-form-urlencoded":
        raise EquipmentError("form_required", 415)
    payload = {field: request.form.get(field) for field in equipment.TEXT_FIELDS}
    for field in ("rated_ac_kw", "pv_dc_kwp"):
        payload[field] = request.form.get(field)
    for field in ("cloud_dependency", "installer_access"):
        payload[field] = request.form.get(field, "unknown")
    payload["evidence"] = _form_evidence("evidence")
    payload["checklist"] = {
        key: {
            "status": request.form.get("checklist." + key + ".status", "unknown"),
            "applicability": request.form.get(
                "checklist." + key + ".applicability", "unknown"
            ),
            "evidence": _form_evidence("checklist." + key + ".evidence"),
        }
        for key in equipment.CHECKS
    }
    payload["vendor_cloud_review"] = {
        "status": request.form.get("vendor_cloud_review.status", "unknown"),
        "remote_access": request.form.get(
            "vendor_cloud_review.remote_access", "unknown"
        ),
        "evidence": _form_evidence("vendor_cloud_review.evidence"),
    }
    payload["backup"] = {
        "capability": request.form.get("backup.capability", "unknown"),
        "installer_evidence": _form_evidence("backup.installer_evidence"),
    }
    return payload


@equipment_bp.get("/dashboard/equipment")
def index():
    return _render("equipment_index.html", records=db.list_equipment(_actor()))


@equipment_bp.route("/dashboard/equipment/new", methods=["GET", "POST"])
@equipment_bp.route("/dashboard/equipment/<uuid:record_id>", methods=["GET", "POST"])
def edit(record_id=None):
    actor = _actor(mutation=request.method == "POST")
    record_id = str(record_id) if record_id else None
    # Check ownership before displaying or accepting edits to an existing record.
    existing = db.get_equipment(actor, record_id) if record_id else None
    if existing is None:
        db.list_equipment(actor)
    if request.method == "POST":
        payload = _form_payload()
        try:
            saved = (
                db.update_equipment(actor, record_id, payload)
                if record_id
                else db.create_equipment(actor, payload)
            )
        except EquipmentError as exc:
            if exc.status != 400:
                raise
            return _render(
                "equipment_form.html",
                record=payload,
                record_id=record_id,
                invalid_field=exc.field,
            ), 400
        return redirect(url_for("equipment.edit", record_id=saved["id"]))
    return _render(
        "equipment_form.html",
        record=existing or equipment.validate_record({}),
        record_id=record_id,
    )


@equipment_bp.post("/dashboard/equipment/<uuid:record_id>/delete")
def remove(record_id):
    db.delete_equipment(_actor(mutation=True), str(record_id))
    return redirect(url_for("equipment.index"))
