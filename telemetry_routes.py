# SPDX-License-Identifier: AGPL-3.0-or-later
"""Owner sessions manage telemetry; installation credentials can only ingest."""

import hmac
import json

from flask import Blueprint, jsonify, request, session

from security_extensions import limiter
from store import telemetry as store
from telemetry import MAX_BYTES, TelemetryError, integer

telemetry_bp = Blueprint(
    "telemetry",
    __name__,
    url_prefix="/api/telemetry/v1/communities/<community>/installations",
)


@telemetry_bp.errorhandler(TelemetryError)
def _error(exc):
    response = jsonify({"error": exc.code})
    response.status_code = exc.status
    if exc.status == 429:
        response.headers["Retry-After"] = "60"
    return response


def _actor(*, mutation=False):
    actor = session.get("dashboard_building_id")
    if not actor:
        raise TelemetryError("session_required", 401)
    if mutation:
        token = request.headers.get("X-CSRF-Token", "")
        expected = session.get("dashboard_csrf_token", "")
        if (
            not token
            or not expected
            or not hmac.compare_digest(token.encode(), expected.encode())
        ):
            raise TelemetryError("csrf_failed", 403)
    return actor


def _payload():
    if request.mimetype != "application/json":
        raise TelemetryError("json_required", 415)
    if request.content_encoding or (request.content_length or 0) > MAX_BYTES:
        raise TelemetryError("payload_too_large", 413)
    raw = request.stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise TelemetryError("payload_too_large", 413)
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise TelemetryError() from None


@telemetry_bp.route("", methods=["POST", "GET"])
def installations(community):
    actor = _actor(mutation=request.method == "POST")
    if request.method == "POST":
        return jsonify(store.enroll(community, actor, _payload())), 201
    return jsonify({"installations": store.list_installations(community, actor)})


@telemetry_bp.route("/<uuid:installation>/samples", methods=["POST", "GET"])
@limiter.limit("120 per minute", methods=["POST"])
def samples(community, installation):
    installation = str(installation)
    if request.method == "GET":
        return _read(community, installation)
    authorization = request.headers.get("Authorization", "")
    token = authorization[7:] if authorization.startswith("Bearer ") else ""
    store.charge_ingestion(community, installation, token)
    return jsonify(store.ingest(community, installation, token, _payload())), 201


def _read(community, installation):
    try:
        after = integer(int(request.args.get("after", "0")), 0, 2**63 - 1)
    except ValueError:
        raise TelemetryError() from None
    return jsonify(store.read(community, str(installation), _actor(), after=after))


@telemetry_bp.get("/<uuid:installation>/export")
def export(community, installation):
    return _read(community, installation)


@telemetry_bp.get("/<uuid:installation>/aggregates")
def aggregates(community, installation):
    return jsonify(store.read(community, str(installation), _actor(), aggregates=True))


@telemetry_bp.delete("/<uuid:installation>")
def delete(community, installation):
    return jsonify(
        store.change(community, str(installation), _actor(mutation=True), "delete")
    )


@telemetry_bp.post("/<uuid:installation>/<action>")
def change(community, installation, action):
    actor = _actor(mutation=True)
    payload = _payload() if action in ("share", "unshare", "retention") else None
    return jsonify(store.change(community, str(installation), actor, action, payload))
