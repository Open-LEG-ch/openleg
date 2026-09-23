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
import database as db
import formation_wizard
import sdat_ingestion
import vnb_exchange

API_SCHEMA_VERSION = "operator-api/1"
EVENT_SCHEMA_VERSION = "operator-event/1"
MAX_WEBHOOK_BATCH_SIZE = 100
CAPABILITIES = frozenset(
    {
        "formation.read",
        "formation.mutate",
        "membership.read",
        "membership.mutate",
        "metering.read",
        "metering.mutate",
        "billing.read",
        "cases.read",
        "cases.mutate",
        "payments.read",
        "payments.mutate",
    }
)
operator_api_bp = Blueprint("operator_api", __name__)


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
    try:
        outcome = vnb_exchange.submit_formation(
            vnb_exchange.FormationSubmission(
                community_id, g.operator_client["created_by"]
            )
        )
    except vnb_exchange.FormationSubmissionForbidden:
        return _error("Formation submission denied", 403)
    except (vnb_exchange.FormationSubmissionInvalid, db.VnbSubmissionConflict):
        # Domain messages stay internal; the public error vocabulary is fixed.
        return _error("Formation submission invalid or conflicting", 409)
    except db.VnbExchangeStoreError:
        return _error("Formation submission unavailable", 503)
    return jsonify(
        schema_version=API_SCHEMA_VERSION,
        state=outcome.state,
        case_id=outcome.case_id,
        event_id=outcome.event_id,
    ), 202


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
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("JSON object required", 400)
    try:
        outcome = vnb_exchange.submit_membership_mutation(
            vnb_exchange.ParticipantMutationSubmission(
                str(payload.get("mutation_id", "")),
                community_id,
                str(payload.get("participant_id", "")),
                g.operator_client["created_by"],
                str(payload.get("mutation_type", "")),
                str(payload.get("effective_date", "")),
                str(payload.get("source_agreement_id", "")),
                payload.get("after") if isinstance(payload.get("after"), dict) else {},
            )
        )
    except vnb_exchange.FormationSubmissionForbidden:
        return _error("Membership mutation denied", 403)
    except (
        vnb_exchange.ParticipantMutationInvalid,
        ValueError,
        db.VnbSubmissionConflict,
    ):
        # Domain messages stay internal; the public error vocabulary is fixed.
        return _error("Membership mutation invalid or conflicting", 409)
    except db.VnbExchangeStoreError:
        return _error("Membership mutation unavailable", 503)
    return jsonify(
        schema_version=API_SCHEMA_VERSION,
        state=outcome.state,
        case_id=outcome.case_id,
        event_id=outcome.event_id,
    ), 202


def _page_args():
    try:
        limit = int(request.args.get("limit", 50))
        cursor = request.args.get("cursor")
        if limit < 1 or limit > 100 or (cursor is not None and int(cursor) < 0):
            raise ValueError
    except (TypeError, ValueError):
        return None
    return {
        "status": request.args.get("status") or None,
        "limit": limit,
        "cursor": cursor,
    }


def _page(read, community_id, safe):
    arguments = _page_args()
    if arguments is None:
        return _error("Invalid pagination", 400)
    rows, next_cursor = read(community_id, **arguments)
    return jsonify(
        schema_version=API_SCHEMA_VERSION,
        items=[safe(row) for row in rows],
        next_cursor=str(next_cursor) if next_cursor is not None else None,
    )


def _safe_fields(*names):
    allowed = frozenset(names)
    return lambda row: {key: value for key, value in row.items() if key in allowed}


_safe_job = _safe_fields(
    "id",
    "territory",
    "started_at",
    "finished_at",
    "status",
    "attempts",
    "downloaded_files",
    "imported_files",
    "imported_readings",
    "error_code",
)
_safe_delivery = _safe_fields(
    "id",
    "contract_version",
    "format_version",
    "transport",
    "community_id",
    "period_start",
    "period_end",
    "status",
    "diagnostics",
    "record_count",
    "received_at",
)
_safe_period = _safe_fields(
    "id",
    "community_id",
    "period_start",
    "period_end",
    "total_production_kwh",
    "total_allocated_kwh",
    "total_surplus_kwh",
    "total_network_discount_chf",
    "status",
)
_safe_invoice = _safe_fields(
    "id", "invoice_number", "gross_chf", "issue_date", "due_date", "lifecycle_state"
)
_safe_invoice_case = _safe_fields(
    "id", "invoice_id", "category", "status", "created_at", "updated_at"
)
_safe_payment = _safe_fields(
    "id",
    "invoice_id",
    "entry_reference",
    "booking_date",
    "amount",
    "currency",
    "payment_reference",
    "is_reversal",
    "match_decision",
)


@operator_api_bp.get("/api/operator/v1/communities/<community_id>/metering/jobs")
@require_operator("metering.read")
def metering_jobs(community_id):
    return _page(db.list_operator_metering_jobs, community_id, _safe_job)


@operator_api_bp.get(
    "/api/operator/v1/communities/<community_id>/metering/calculated-deliveries"
)
@require_operator("metering.read")
def calculated_deliveries(community_id):
    return _page(db.list_operator_calculated_deliveries, community_id, _safe_delivery)


@operator_api_bp.post(
    "/api/operator/v1/communities/<community_id>/metering/jobs/<int:job_id>/retry"
)
@require_operator("metering.mutate")
def retry_metering_job(community_id, job_id):
    key = _idempotency_key()
    if not key:
        return _error("Valid Idempotency-Key required", 400)
    claim = db.claim_operator_ingestion_retry(community_id, job_id, key)
    if not claim:
        return _error("Resource not found or not eligible", 404)
    if claim.get("pending"):
        return _error("Retry already in progress", 409)
    if claim.get("replay"):
        return jsonify(schema_version=API_SCHEMA_VERSION, **claim["replay"])
    schedule = claim["schedule"]
    try:
        result = sdat_ingestion.run(schedule["territory"], schedule)
    except Exception:
        db.release_operator_ingestion_retry(community_id, job_id, key)
        raise
    db.complete_operator_ingestion_retry(community_id, job_id, key, result)
    return jsonify(schema_version=API_SCHEMA_VERSION, **result)


@operator_api_bp.get("/api/operator/v1/communities/<community_id>/billing/periods")
@require_operator("billing.read")
def billing_periods(community_id):
    return _page(db.list_operator_billing_periods, community_id, _safe_period)


@operator_api_bp.get("/api/operator/v1/communities/<community_id>/billing/invoices")
@require_operator("billing.read")
def billing_invoices(community_id):
    return _page(db.list_operator_invoices, community_id, _safe_invoice)


@operator_api_bp.get("/api/operator/v1/communities/<community_id>/billing/cases")
@require_operator("cases.read")
def invoice_cases(community_id):
    return _page(db.list_operator_invoice_cases, community_id, _safe_invoice_case)


@operator_api_bp.get("/api/operator/v1/communities/<community_id>/payments/matches")
@require_operator("payments.read")
def payment_matches(community_id):
    return _page(db.list_operator_payment_matches, community_id, _safe_payment)


def _idempotency_key():
    value = request.headers.get("Idempotency-Key", "")
    return value if 1 <= len(value) <= 128 else None


@operator_api_bp.post(
    "/api/operator/v1/communities/<community_id>/billing/cases/<int:case_id>/responses"
)
@require_operator("cases.mutate")
def respond_invoice_case(community_id, case_id):
    key = _idempotency_key()
    if not key:
        return _error("Valid Idempotency-Key required", 400)
    payload = request.get_json(silent=True) or {}
    try:
        result = db.respond_operator_invoice_case(
            case_id,
            community_id,
            g.operator_client["created_by"],
            payload.get("message", ""),
            payload.get("status", ""),
            key,
        )
    except ValueError:
        # Store validation messages stay internal; the API answer is fixed.
        return _error("Invoice case update invalid", 409)
    return (
        jsonify(schema_version=API_SCHEMA_VERSION, **result)
        if result
        else _error("Resource not found", 404)
    )


@operator_api_bp.post(
    "/api/operator/v1/communities/<community_id>/payments/matches/<int:entry_id>/confirm"
)
@require_operator("payments.mutate")
def confirm_payment_match(community_id, entry_id):
    key = _idempotency_key()
    if not key:
        return _error("Valid Idempotency-Key required", 400)
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload.get("invoice_id"), int):
        return _error("Invalid payment confirmation", 400)
    try:
        result = db.confirm_operator_payment_match(
            entry_id,
            payload["invoice_id"],
            community_id,
            g.operator_client["created_by"],
            key,
        )
    except (TypeError, ValueError):
        # Store validation messages stay internal; the API answer is fixed.
        return _error("Invalid payment confirmation", 409)
    return (
        jsonify(schema_version=API_SCHEMA_VERSION, **result)
        if result
        else _error("Resource not found or not eligible", 404)
    )


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
    if not all(isinstance(item, str) for item in raw_capabilities):
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
        credential=_safe_credential(row),
        token=token,
        webhook_secret=webhook_secret,
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


def _public_webhook_addresses(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return ()
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                parsed.hostname, 443, type=socket.SOCK_STREAM
            )
        }
    except socket.gaierror:
        return ()
    return (
        tuple(sorted(addresses))
        if addresses and all(ip_address(value).is_global for value in addresses)
        else ()
    )


def _is_public_webhook_url(url):
    return bool(_public_webhook_addresses(url))


def _default_transport(url, body, headers, timeout):
    parsed = urlparse(url)
    addresses = _public_webhook_addresses(url)
    if not addresses:
        raise ValueError("Webhook destination is not public")
    connection = _PinnedHTTPSConnection(
        parsed.hostname, addresses[0], parsed.port or 443, timeout=timeout
    )
    try:
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        connection.request("POST", target, body=body, headers=headers)
        return connection.getresponse().status
    finally:
        connection.close()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to a validated address while verifying TLS for the original host."""

    def __init__(self, hostname, address, port, *, timeout):
        super().__init__(
            hostname, port=port, timeout=timeout, context=ssl.create_default_context()
        )
        self._validated_address = address

    def connect(self):
        raw = socket.create_connection(
            (self._validated_address, self.port), self.timeout, self.source_address
        )
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


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
