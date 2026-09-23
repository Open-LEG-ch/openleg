# SPDX-License-Identifier: AGPL-3.0-or-later
"""Private, community-scoped operator API and signed event delivery."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import secrets
import socket
import ssl
import urllib.error
from datetime import date, datetime
from functools import wraps
from ipaddress import ip_address
from urllib.parse import urlparse

from flask import Blueprint, current_app, g, jsonify, request, session

import community_access
import dashboard
import database as db
import formation_wizard

API_SCHEMA_VERSION = "operator-api/1"
EVENT_SCHEMA_VERSION = "operator-event/1"
CAPABILITIES = frozenset(
    {"formation.read", "formation.mutate", "membership.read", "membership.mutate"}
)
operator_api_bp = Blueprint("operator_api", __name__)
MAX_WEBHOOK_BATCH_SIZE = 100


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_secret(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


def _webhook_secret(
    client_id: str, signing_key: str | None = None, version: int = 1
) -> str:
    """Derive an independent stable secret from the instance key and client ID."""
    key = signing_key or current_app.config["SECRET_KEY"]
    digest = hmac.new(
        key.encode(),
        f"openleg-webhook-v1:{client_id}:{int(version)}".encode(),
        hashlib.sha256,
    )
    return "olwhsec_" + digest.hexdigest()


def sign_webhook(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_webhook_signature(body: bytes, signature: str, secret: str) -> bool:
    return isinstance(signature, str) and hmac.compare_digest(
        sign_webhook(body, secret), signature
    )


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError


def _safe_case(row: dict) -> dict:
    blocked = {
        "manual_package",
        "response_evidence",
        "evidence",
        "created_by",
        "delivered_by",
        "before_facts",
        "after_facts",
        "capability_snapshot",
    }
    return {key: value for key, value in row.items() if key not in blocked}


def _error(message, status):
    response = jsonify({"error": message, "schema_version": API_SCHEMA_VERSION})
    response.status_code = status
    response.headers.update(
        {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    )
    return response


def require_operator(capability):
    def decorator(view):
        @wraps(view)
        def wrapped(community_id, *args, **kwargs):
            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                return _error("Authentication required", 401)
            client = db.get_operator_api_client_by_token_hash(
                _token_hash(header.removeprefix("Bearer ").strip())
            )
            if not client:
                return _error("Invalid or revoked credential", 401)
            if client["community_id"] != community_id:
                return _error("Resource not found", 404)
            if capability not in set(client.get("capabilities") or []):
                return _error("Capability denied", 403)
            if not db.claim_operator_api_usage(
                client["id"], request.path, int(client["rate_limit_per_hour"])
            ):
                response = _error("Rate limit exceeded", 429)
                response.headers["Retry-After"] = "3600"
                return response
            g.operator_client = client
            return view(community_id, *args, **kwargs)

        return wrapped

    return decorator


@operator_api_bp.after_request
def private_api_headers(response):
    response.headers.update(
        {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    )
    return response


@operator_api_bp.get("/api/operator/v1/communities/<community_id>/formation")
@require_operator("formation.read")
def formation_status(community_id):
    return jsonify(
        schema_version=API_SCHEMA_VERSION,
        submissions=[
            _safe_case(row) for row in db.list_vnb_submission_cases(community_id)
        ],
    )


@operator_api_bp.post(
    "/api/operator/v1/communities/<community_id>/formation/submissions"
)
@require_operator("formation.mutate")
def submit_formation(community_id):
    result = dashboard.leg_submit_vnb_formation(
        community_id, g.operator_client["created_by"]
    )
    if result.get("error"):
        return _error(result["error"], result.get("error_status", 409))
    return jsonify(schema_version=API_SCHEMA_VERSION, **result), 202


@operator_api_bp.get("/api/operator/v1/communities/<community_id>/membership-mutations")
@require_operator("membership.read")
def membership_mutations(community_id):
    return jsonify(
        schema_version=API_SCHEMA_VERSION,
        mutations=[_safe_case(row) for row in db.list_vnb_mutations(community_id)],
    )


@operator_api_bp.post(
    "/api/operator/v1/communities/<community_id>/membership-mutations"
)
@require_operator("membership.mutate")
def submit_membership_mutation(community_id):
    payload = request.get_json(silent=True) or {}
    required = (
        "mutation_id",
        "participant_id",
        "mutation_type",
        "effective_date",
        "source_agreement_id",
    )
    if any(
        not isinstance(payload.get(key), str) or not payload[key].strip()
        for key in required
    ):
        return _error("Required mutation fields are missing", 400)
    after = payload.get("after", {})
    if not isinstance(after, dict):
        return _error("after must be an object", 400)
    result = dashboard.leg_submit_vnb_mutation(
        community_id,
        g.operator_client["created_by"],
        payload["mutation_id"],
        payload["participant_id"],
        payload["mutation_type"],
        payload["effective_date"],
        payload["source_agreement_id"],
        after,
    )
    if result.get("error"):
        return _error(result["error"], result.get("error_status", 409))
    return jsonify(schema_version=API_SCHEMA_VERSION, **result), 202


def _admin_for(community_id: str, building_id: str | None) -> bool:
    status = formation_wizard.get_community_status(community_id)
    member = next(
        (
            row
            for row in (status or {}).get("members", [])
            if row["building_id"] == building_id
        ),
        None,
    )
    return bool(
        member
        and community_access.is_administrator(member)
        and member.get("status") == "confirmed"
    )


def _require_csrf():
    submitted = request.headers.get("X-CSRF-Token") or request.form.get(
        "csrf_token", ""
    )
    expected = session.get("dashboard_csrf_token", "")
    return bool(
        isinstance(submitted, str)
        and isinstance(expected, str)
        and expected
        and hmac.compare_digest(submitted, expected)
    )


def _safe_credential(row):
    allowed = {
        "id",
        "community_id",
        "name",
        "capabilities",
        "rate_limit_per_hour",
        "active",
        "created_at",
        "updated_at",
        "last_used_at",
        "webhook_url",
    }
    return {key: value for key, value in row.items() if key in allowed}


@operator_api_bp.post("/leg/community/<community_id>/operator-api/credentials")
def create_credential(community_id):
    building_id = session.get("dashboard_building_id")
    if not _admin_for(community_id, building_id):
        return _error("Forbidden", 403)
    if not _require_csrf():
        return _error("Invalid CSRF token", 400)
    if request.is_json:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return _error("JSON body must be an object", 400)
        raw_capabilities = payload.get("capabilities")
        if not isinstance(raw_capabilities, list):
            return _error("Capabilities must be a list", 400)
    else:
        payload = request.form
        raw_capabilities = payload.getlist("capabilities")
    if not all(isinstance(value, str) for value in raw_capabilities):
        return _error("Invalid capabilities", 400)
    capabilities = sorted(set(raw_capabilities))
    if not capabilities or not set(capabilities) <= CAPABILITIES:
        return _error("Invalid capabilities", 400)
    name = str(payload.get("name", "")).strip()
    webhook_url = str(payload.get("webhook_url", "")).strip() or None
    if not name:
        return _error("Credential name is required", 400)
    if webhook_url:
        parsed = urlparse(webhook_url)
        if parsed.scheme != "https" or not parsed.hostname:
            return _error("Webhook URL must use HTTPS", 400)
        if not _is_public_webhook_url(webhook_url):
            return _error("Webhook host must be publicly routable", 400)
    token = _new_secret("olk_")
    token_hash = _token_hash(token)
    row = db.create_operator_api_client(
        community_id,
        building_id,
        name,
        capabilities,
        token_hash,
        webhook_url,
    )
    webhook_secret = _webhook_secret(
        row["id"], version=row.get("webhook_secret_version", 1)
    )
    return jsonify(
        credential=_safe_credential(row), token=token, webhook_secret=webhook_secret
    ), 201


@operator_api_bp.get("/leg/community/<community_id>/operator-api/credentials")
def list_credentials(community_id):
    if not _admin_for(community_id, session.get("dashboard_building_id")):
        return _error("Forbidden", 403)
    if not _require_csrf():
        return _error("Invalid CSRF token", 400)
    return jsonify(
        credentials=[
            _safe_credential(row) for row in db.list_operator_api_clients(community_id)
        ]
    )


@operator_api_bp.post(
    "/leg/community/<community_id>/operator-api/credentials/<client_id>/rotate"
)
def rotate_credential(community_id, client_id):
    if not _admin_for(community_id, session.get("dashboard_building_id")):
        return _error("Forbidden", 403)
    if not _require_csrf():
        return _error("Invalid CSRF token", 400)
    token = _new_secret("olk_")
    token_hash = _token_hash(token)
    row = db.rotate_operator_api_client(community_id, client_id, token_hash)
    return (
        jsonify(
            credential=_safe_credential(row),
            token=token,
            webhook_secret=_webhook_secret(
                row["id"], version=row.get("webhook_secret_version", 1)
            ),
        )
        if row
        else _error("Credential not found", 404)
    )


@operator_api_bp.post(
    "/leg/community/<community_id>/operator-api/credentials/<client_id>/revoke"
)
def revoke_credential(community_id, client_id):
    if not _require_csrf():
        return _error("Invalid CSRF token", 400)
    if not _admin_for(community_id, session.get("dashboard_building_id")):
        return _error("Forbidden", 403)
    row = db.revoke_operator_api_client(community_id, client_id)
    return (
        jsonify(credential=_safe_credential(row))
        if row
        else _error("Credential not found", 404)
    )


@operator_api_bp.get("/leg/community/<community_id>/operator-api/deliveries")
def list_webhook_deliveries(community_id):
    if not _admin_for(community_id, session.get("dashboard_building_id")):
        return _error("Forbidden", 403)
    return jsonify(deliveries=db.list_operator_webhook_deliveries(community_id))


@operator_api_bp.post(
    "/leg/community/<community_id>/operator-api/deliveries/<delivery_id>/retry"
)
def retry_webhook_delivery(community_id, delivery_id):
    if not _admin_for(community_id, session.get("dashboard_building_id")):
        return _error("Forbidden", 403)
    if not _require_csrf():
        return _error("Invalid CSRF token", 400)
    delivery = db.retry_operator_webhook_delivery(community_id, delivery_id)
    return jsonify(delivery=delivery) if delivery else _error("Delivery not found", 404)


def _is_public_webhook_url(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                parsed.hostname, 443, type=socket.SOCK_STREAM
            )
        }
    except socket.gaierror:
        return False
    return bool(addresses) and all(ip_address(value).is_global for value in addresses)


def _resolve_public_webhook(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Webhook destination is not public")
    port = parsed.port or 443
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                parsed.hostname, port, type=socket.SOCK_STREAM
            )
        }
    except socket.gaierror as exc:
        raise ValueError("Webhook destination is not public") from exc
    if not addresses or not all(ip_address(value).is_global for value in addresses):
        raise ValueError("Webhook destination is not public")
    return parsed, port, min(addresses)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname, pinned_ip, **kwargs):
        super().__init__(hostname, **kwargs)
        self._pinned_ip = pinned_ip

    def connect(self):
        sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _default_transport(url, body, headers, timeout):
    parsed, port, pinned_ip = _resolve_public_webhook(url)
    connection = _PinnedHTTPSConnection(
        parsed.hostname,
        pinned_ip,
        port=port,
        timeout=timeout,
        context=ssl.create_default_context(),
    )
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    try:
        connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status
    finally:
        connection.close()


def dispatch_pending_webhooks(
    *, transport=None, max_attempts=5, batch_size=50, signing_key=None
):
    transport = transport or _default_transport
    batch_size = max(1, min(int(batch_size), MAX_WEBHOOK_BATCH_SIZE))
    totals = {"attempted": 0, "delivered": 0, "failed": 0}
    for row in db.get_pending_webhook_deliveries(
        max_attempts=max_attempts, limit=batch_size
    ):
        totals["attempted"] += 1
        if (
            row.get("status") != "processing"
            or row.get("schema_version") != EVENT_SCHEMA_VERSION
        ):
            db.record_webhook_attempt(
                row["delivery_id"],
                claim_id=row["claim_id"],
                status="failed",
                response_status=0,
                retryable=False,
            )
            totals["failed"] += 1
            continue
        payload = {
            key: row[key]
            for key in (
                "event_id",
                "event_type",
                "schema_version",
                "aggregate_id",
                "community_id",
                "payload",
                "occurred_at",
            )
        }
        body = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), default=_json_default
        ).encode()
        headers = {
            "Content-Type": "application/json",
            "OpenLEG-Delivery": row["delivery_id"],
            "OpenLEG-Signature": sign_webhook(
                body,
                _webhook_secret(
                    row["client_id"],
                    signing_key,
                    row.get("webhook_secret_version", 1),
                ),
            ),
        }
        try:
            status = transport(row["webhook_url"], body, headers, 10)
        except urllib.error.HTTPError as error:
            status = error.code
        except Exception:
            status = 0
        delivered = 200 <= status < 300
        retryable = not delivered and row["attempt_count"] + 1 < max_attempts
        state = "delivered" if delivered else ("retry" if retryable else "failed")
        db.record_webhook_attempt(
            row["delivery_id"],
            claim_id=row["claim_id"],
            status=state,
            response_status=status,
            retryable=retryable,
        )
        totals["delivered" if delivered else "failed"] += 1
    return totals
