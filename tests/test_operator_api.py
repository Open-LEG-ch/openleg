# SPDX-License-Identifier: AGPL-3.0-or-later
"""Authenticated HTTP and signed-webhook contracts for operator integrations."""

import hashlib
import hmac
import json
import urllib.error
from unittest.mock import patch

import operator_api


def _register(app):
    if "operator_api" not in app.blueprints:
        app.register_blueprint(operator_api.operator_api_bp)
    return app.test_client()


CLIENT = {
    "id": "client-1",
    "community_id": "community-a",
    "created_by": "admin-a",
    "name": "Integrator",
    "capabilities": ["formation.read", "formation.mutate", "membership.mutate"],
    "rate_limit_per_hour": 10,
    "active": True,
}


def _auth():
    return {"Authorization": "Bearer olk_test-secret"}


@patch("operator_api.db.claim_operator_api_usage", return_value=True)
@patch("operator_api.db.get_operator_api_client_by_token_hash", return_value=CLIENT)
@patch("operator_api.db.list_vnb_submission_cases")
def test_formation_status_is_authenticated_tenant_scoped_and_private(
    list_cases, _lookup, _usage, app
):
    client = _register(app)
    list_cases.return_value = [
        {
            "case_id": "case-1",
            "community_id": "community-a",
            "manual_package": b"private",
        }
    ]

    response = client.get(
        "/api/operator/v1/communities/community-a/formation", headers=_auth()
    )
    denied = client.get(
        "/api/operator/v1/communities/community-b/formation", headers=_auth()
    )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json() == {
        "schema_version": "operator-api/1",
        "submissions": [{"case_id": "case-1", "community_id": "community-a"}],
    }
    assert denied.status_code == 404
    list_cases.assert_called_once_with("community-a")


@patch("operator_api.db.claim_operator_api_usage", return_value=True)
@patch("operator_api.db.get_operator_api_client_by_token_hash")
def test_capability_revocation_and_rate_limit_apply_at_http_boundary(
    lookup, usage, app
):
    client = _register(app)
    lookup.side_effect = [{**CLIENT, "capabilities": ["formation.read"]}, None, CLIENT]

    capability = client.post(
        "/api/operator/v1/communities/community-a/membership-mutations",
        headers=_auth(),
        json={},
    )
    revoked = client.get(
        "/api/operator/v1/communities/community-a/formation", headers=_auth()
    )
    usage.return_value = False
    limited = client.get(
        "/api/operator/v1/communities/community-a/formation", headers=_auth()
    )

    assert capability.status_code == 403
    assert revoked.status_code == 401
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "3600"


@patch("operator_api.db.claim_operator_api_usage", return_value=True)
@patch("operator_api.db.get_operator_api_client_by_token_hash", return_value=CLIENT)
@patch("operator_api.dashboard.leg_submit_vnb_mutation")
def test_membership_mutation_uses_ui_domain_seam_and_stable_event(
    submit, _lookup, _usage, app
):
    client = _register(app)
    submit.return_value = {
        "error": None,
        "state": "prepared",
        "case_id": "case-7",
        "event_id": "evt-7",
    }
    payload = {
        "mutation_id": "m-7",
        "participant_id": "p-1",
        "mutation_type": "join",
        "effective_date": "2026-10-01",
        "source_agreement_id": "agreement-1",
        "after": {"metering_point_id": "CH1"},
    }

    response = client.post(
        "/api/operator/v1/communities/community-a/membership-mutations",
        headers=_auth(),
        json=payload,
    )

    assert response.status_code == 202
    submit.assert_called_once_with(
        "community-a",
        "admin-a",
        "m-7",
        "p-1",
        "join",
        "2026-10-01",
        "agreement-1",
        {"metering_point_id": "CH1"},
    )
    assert response.get_json()["event_id"] == "evt-7"


def test_webhook_signature_verification_rejects_tampering_and_replays_are_idempotent():
    body = b'{"event_id":"evt-1"}'
    signature = operator_api.sign_webhook(body, "secret")
    assert operator_api.verify_webhook_signature(body, signature, "secret")
    assert not operator_api.verify_webhook_signature(body + b" ", signature, "secret")
    assert not operator_api.verify_webhook_signature(body, "sha256=bad", "secret")


@patch("operator_api.db.create_operator_api_client")
@patch("operator_api.db.revoke_operator_api_client")
@patch("operator_api.db.rotate_operator_api_client")
@patch("operator_api._is_public_webhook_url", return_value=True)
@patch("operator_api.formation_wizard.get_community_status")
def test_admin_creates_show_once_hashed_credential_and_can_rotate_and_revoke(
    status, _public_url, rotate, revoke, create, app
):
    status.return_value = {
        "members": [{"building_id": "admin-a", "role": "admin", "status": "confirmed"}]
    }
    create.return_value = {
        **CLIENT,
        "token_hash": "must-not-leak",
        "webhook_secret": "must-not-leak",
    }
    rotate.return_value = CLIENT
    revoke.return_value = {**CLIENT, "active": False}
    app.secret_key = "operator-api-test"
    client = _register(app)
    with client.session_transaction() as current:
        current["dashboard_building_id"] = "admin-a"
        current["dashboard_csrf_token"] = "csrf-test"

    response = client.post(
        "/leg/community/community-a/operator-api/credentials",
        headers={"X-CSRF-Token": "csrf-test"},
        json={
            "name": "Integrator",
            "capabilities": ["formation.read"],
            "webhook_url": "https://operator.example/events",
        },
    )

    assert response.status_code == 201
    shown = response.get_json()
    assert shown["token"].startswith("olk_")
    assert shown["webhook_secret"].startswith("olwhsec_")
    assert shown["webhook_secret"] == operator_api._webhook_secret(
        "client-1", app.secret_key
    )
    assert "token_hash" not in shown["credential"]
    stored_hash = create.call_args.args[4]
    assert stored_hash == hashlib.sha256(shown["token"].encode()).hexdigest()
    assert shown["token"] not in repr(create.call_args)
    rotated = client.post(
        "/leg/community/community-a/operator-api/credentials/client-1/rotate",
        headers={"X-CSRF-Token": "csrf-test"},
    )
    revoked = client.post(
        "/leg/community/community-a/operator-api/credentials/client-1/revoke",
        headers={"X-CSRF-Token": "csrf-test"},
    )
    assert rotated.status_code == 200
    assert rotated.get_json()["token"] != shown["token"]
    assert rotated.get_json()["webhook_secret"] == operator_api._webhook_secret(
        "client-1", app.secret_key, 1
    )
    assert revoked.status_code == 200
    assert revoked.get_json()["credential"]["active"] is False


@patch("operator_api.formation_wizard.get_community_status")
def test_credential_mutation_rejects_missing_csrf(status, app):
    status.return_value = {
        "members": [{"building_id": "admin-a", "role": "admin", "status": "confirmed"}]
    }
    app.secret_key = "operator-api-test"
    client = _register(app)
    with client.session_transaction() as current:
        current["dashboard_building_id"] = "admin-a"
        current["dashboard_csrf_token"] = "csrf-test"

    response = client.post(
        "/leg/community/community-a/operator-api/credentials",
        json={"name": "Attacker", "capabilities": ["formation.read"]},
    )

    assert response.status_code == 400


@patch("operator_api.db.revoke_operator_api_client")
@patch("operator_api.formation_wizard.get_community_status")
def test_revoke_rejects_csrf_before_authorization_lookup(status, revoke, app):
    app.secret_key = "operator-api-test"
    client = _register(app)
    with client.session_transaction() as current:
        current["dashboard_building_id"] = "admin-a"
        current["dashboard_csrf_token"] = "csrf-test"

    response = client.post(
        "/leg/community/community-a/operator-api/credentials/client-1/revoke"
    )

    assert response.status_code == 400
    status.assert_not_called()
    revoke.assert_not_called()


@patch("operator_api.db.create_operator_api_client")
@patch("operator_api.formation_wizard.get_community_status")
def test_credential_capabilities_normalize_repeated_form_and_reject_scalar_json(
    status, create, app
):
    status.return_value = {
        "members": [{"building_id": "admin-a", "role": "admin", "status": "confirmed"}]
    }
    create.return_value = CLIENT
    app.secret_key = "operator-api-test"
    client = _register(app)
    with client.session_transaction() as current:
        current["dashboard_building_id"] = "admin-a"
        current["dashboard_csrf_token"] = "csrf-test"

    scalar = client.post(
        "/leg/community/community-a/operator-api/credentials",
        headers={"X-CSRF-Token": "csrf-test"},
        json="formation.read",
    )
    form = client.post(
        "/leg/community/community-a/operator-api/credentials",
        data={
            "csrf_token": "csrf-test",
            "name": "Forms",
            "capabilities": ["membership.read", "formation.read"],
        },
    )

    assert scalar.status_code == 400
    assert form.status_code == 201
    assert create.call_args.args[3] == ["formation.read", "membership.read"]


@patch("operator_api.socket.getaddrinfo")
def test_webhook_destination_rejects_private_and_loopback_addresses(resolve):
    resolve.side_effect = [
        [(None, None, None, None, ("127.0.0.1", 443))],
        [(None, None, None, None, ("10.0.0.5", 443))],
    ]
    assert not operator_api._is_public_webhook_url("https://localhost/events")
    assert not operator_api._is_public_webhook_url("https://internal.example/events")


@patch("operator_api.ssl.create_default_context", return_value=object())
@patch("operator_api.socket.getaddrinfo")
def test_default_webhook_transport_pins_the_validated_address(
    resolve, tls, monkeypatch
):
    resolve.return_value = [
        (None, None, None, None, ("93.184.216.34", 443)),
    ]
    calls = []

    class Connection:
        def __init__(self, hostname, pinned_ip, **kwargs):
            calls.append((hostname, pinned_ip, kwargs))

        def request(self, method, path, body, headers):
            calls.append((method, path, body, headers))

        def getresponse(self):
            return type("Response", (), {"status": 204})()

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(operator_api, "_PinnedHTTPSConnection", Connection)

    status = operator_api._default_transport(
        "https://operator.example/events?q=1", b"{}", {"X-Test": "1"}, 10
    )

    assert status == 204
    assert calls[0][0:2] == ("operator.example", "93.184.216.34")
    assert calls[1][0:2] == ("POST", "/events?q=1")
    assert resolve.call_count == 1
    assert calls[-1] == "closed"


@patch("operator_api.db.record_webhook_attempt")
@patch("operator_api.db.get_pending_webhook_deliveries")
def test_webhook_dispatch_signs_payload_bounds_retries_and_records_failure(
    pending, record
):
    event = {
        "event_id": "evt-1",
        "event_type": "formation.submission.prepared",
        "schema_version": "operator-event/1",
        "aggregate_id": "case-1",
        "community_id": "community-a",
        "payload": {"state": "prepared"},
        "occurred_at": "2026-09-15T12:00:00+00:00",
        "delivery_id": "delivery-1",
        "client_id": "client-1",
        "status": "processing",
        "claim_id": "claim-1",
        "webhook_url": "https://operator.example/hook",
        "attempt_count": 2,
    }
    pending.return_value = [event]
    sent = []

    def transport(url, body, headers, timeout):
        sent.append((url, body, headers, timeout))
        return 503

    assert operator_api.dispatch_pending_webhooks(
        transport=transport, max_attempts=3, signing_key="instance-secret"
    ) == {"attempted": 1, "delivered": 0, "failed": 1}
    body = sent[0][1]
    assert json.loads(body)["event_id"] == "evt-1"
    assert hmac.compare_digest(
        sent[0][2]["OpenLEG-Signature"],
        operator_api.sign_webhook(
            body,
            operator_api._webhook_secret("client-1", "instance-secret"),
        ),
    )
    record.assert_called_once_with(
        "delivery-1",
        claim_id="claim-1",
        status="failed",
        response_status=503,
        retryable=False,
    )


@patch("operator_api.db.record_webhook_attempt")
@patch("operator_api.db.get_pending_webhook_deliveries")
def test_webhook_dispatch_records_http_error_status(pending, record):
    event = {
        "event_id": "evt-1",
        "event_type": "formation.submission.prepared",
        "schema_version": "operator-event/1",
        "aggregate_id": "case-1",
        "community_id": "community-a",
        "payload": {},
        "occurred_at": "2026-09-15T12:00:00+00:00",
        "delivery_id": "delivery-1",
        "client_id": "client-1",
        "status": "processing",
        "claim_id": "claim-1",
        "webhook_url": "https://operator.example/hook",
        "attempt_count": 0,
    }
    pending.return_value = [event]

    def transport(*_args):
        raise urllib.error.HTTPError("https://operator.example/hook", 429, "", {}, None)

    operator_api.dispatch_pending_webhooks(
        transport=transport, max_attempts=3, signing_key="instance-secret"
    )

    assert record.call_args.kwargs["response_status"] == 429
    assert record.call_args.kwargs["retryable"] is True
