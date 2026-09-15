# SPDX-License-Identifier: AGPL-3.0-or-later
"""Private, community-scoped operator API and signed event delivery."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import socket
import urllib.request
from datetime import date, datetime
from functools import wraps
from ipaddress import ip_address
from urllib.parse import urlparse

from flask import Blueprint, g, jsonify, request, session

import community_access
import database as db
import formation_wizard

API_SCHEMA_VERSION = "operator-api/1"
EVENT_SCHEMA_VERSION = "operator-event/1"
CAPABILITIES = frozenset(
    {"formation.read", "formation.mutate", "membership.read", "membership.mutate"}
)
operator_api_bp = Blueprint("operator_api", __name__)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_secret(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


def _webhook_secret(token_hash: str) -> str:
    """Derive a signing value without persisting recoverable secret material."""
    digest = hmac.new(token_hash.encode(), b"openleg-webhook-v1", hashlib.sha256)
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
    payload = request.get_json(silent=True) or request.form
    capabilities = sorted(set(payload.get("capabilities", [])))
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
    webhook_secret = _webhook_secret(token_hash)
    row = db.create_operator_api_client(
        community_id,
        building_id,
        name,
        capabilities,
        token_hash,
        webhook_url,
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
            webhook_secret=_webhook_secret(token_hash),
        )
        if row
        else _error("Credential not found", 404)
    )


@operator_api_bp.post(
    "/leg/community/<community_id>/operator-api/credentials/<client_id>/revoke"
)
def revoke_credential(community_id, client_id):
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_transport(url, body, headers, timeout):
    if not _is_public_webhook_url(url):
        raise ValueError("Webhook destination is not public")
    request_object = urllib.request.Request(
        url, data=body, headers=headers, method="POST"
    )
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(request_object, timeout=timeout) as response:
        return response.status


def dispatch_pending_webhooks(*, transport=None, max_attempts=5):
    transport = transport or _default_transport
    totals = {"attempted": 0, "delivered": 0, "failed": 0}
    for row in db.get_pending_webhook_deliveries(max_attempts=max_attempts):
        totals["attempted"] += 1
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
            "OpenLEG-Signature": sign_webhook(body, _webhook_secret(row["token_hash"])),
        }
        try:
            status = transport(row["webhook_url"], body, headers, 10)
        except Exception:
            status = 0
        delivered = 200 <= status < 300
        retryable = not delivered and row["attempt_count"] + 1 < max_attempts
        state = "delivered" if delivered else ("retry" if retryable else "failed")
        db.record_webhook_attempt(
            row["delivery_id"],
            status=state,
            response_status=status,
            retryable=retryable,
        )
        totals["delivered" if delivered else "failed"] += 1
    return totals
