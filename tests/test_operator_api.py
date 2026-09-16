# SPDX-License-Identifier: AGPL-3.0-or-later
"""Authenticated HTTP and signed-webhook contracts for operator integrations."""

import json
from types import SimpleNamespace
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
@patch("operator_api.vnb_exchange.submit_membership_mutation")
def test_membership_mutation_uses_shared_domain_seam_and_stable_event(
    submit, _lookup, _usage, app
):
    client = _register(app)
    submit.return_value = SimpleNamespace(
        state="prepared", case_id="case-7", event_id="evt-7"
    )
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
    command = submit.call_args.args[0]
    assert command.community_id == "community-a"
    assert command.actor_building_id == "admin-a"
    assert command.mutation_id == "m-7"
    assert command.after == {"metering_point_id": "CH1"}
    assert response.get_json()["event_id"] == "evt-7"


@patch("operator_api.db.claim_operator_api_usage", return_value=True)
@patch("operator_api.db.get_operator_api_client_by_token_hash", return_value=CLIENT)
@patch("operator_api.vnb_exchange.submit_formation")
def test_formation_submission_uses_shared_domain_seam(submit, _lookup, _usage, app):
    submit.return_value = SimpleNamespace(
        state="prepared", case_id="case-8", event_id="evt-8"
    )
    client = _register(app)

    response = client.post(
        "/api/operator/v1/communities/community-a/formation/submissions",
        headers=_auth(),
    )

    assert response.status_code == 202
    command = submit.call_args.args[0]
    assert command.community_id == "community-a"
    assert command.actor_building_id == "admin-a"
    assert response.get_json()["event_id"] == "evt-8"


def _credential_row(**overrides):
    row = {
        "id": "client-1",
        "community_id": "community-a",
        "created_by": "building-1",
        "name": "Integrator",
        "capabilities": ["formation.read"],
        "rate_limit_per_hour": 100,
        "active": True,
        "created_at": "2026-01-01T08:00:00+00:00",
        "updated_at": "2026-01-01T08:00:00+00:00",
        "webhook_url": None,
        "token_hash": "hash-created",
    }
    row.update(overrides)
    return row


def _delivery_row(**overrides):
    row = {
        "delivery_id": "delivery-1",
        "claim_id": "claim-1",
        "client_id": "client-1",
        "status": "processing",
        "schema_version": "operator-event/1",
        "attempt_count": 0,
        "event_id": "event-1",
        "event_type": "formation.submitted",
        "aggregate_id": "case-1",
        "community_id": "community-a",
        "payload": {"status": "prepared"},
        "occurred_at": "2026-01-01T08:00:00+00:00",
        "webhook_url": "https://hooks.example.net/ingest",
    }
    row.update(overrides)
    return row


def _admin_session(client):
    with client.session_transaction() as session:
        session["dashboard_building_id"] = "building-1"
        session["dashboard_csrf_token"] = "csrf-secret"
    return {"X-CSRF-Token": "csrf-secret"}


@patch("operator_api.db.list_vnb_submission_cases", return_value=[])
@patch("operator_api.db.claim_operator_api_usage", return_value=True)
@patch("operator_api.db.get_operator_api_client_by_token_hash")
@patch("operator_api.db.list_operator_api_clients")
@patch("operator_api.db.revoke_operator_api_client")
@patch("operator_api.db.rotate_operator_api_client")
@patch("operator_api.db.create_operator_api_client")
@patch("operator_api.community_access.is_administrator", return_value=True)
@patch("operator_api.formation_wizard.get_community_status")
def test_credential_lifecycle_shows_secrets_once_and_cuts_access_on_revoke(
    community_status,
    _is_admin,
    create_client,
    rotate_client,
    revoke_client,
    list_clients,
    lookup,
    _usage,
    _cases,
    app,
):
    app.config["SECRET_KEY"] = "credential-lifecycle-key"
    community_status.return_value = {
        "members": [{"building_id": "building-1", "status": "confirmed"}]
    }
    row = _credential_row()
    create_client.return_value = row
    list_clients.return_value = [row]
    client = _register(app)
    csrf = _admin_session(client)

    created = client.post(
        "/leg/community/community-a/operator-api/credentials",
        headers=csrf,
        json={"name": "Integrator", "capabilities": ["formation.read"]},
    )
    listed = client.get(
        "/leg/community/community-a/operator-api/credentials", headers=csrf
    )

    body = created.get_json()
    assert created.status_code == 201
    assert list(create_client.call_args.args)[:4] == [
        "community-a",
        "building-1",
        "Integrator",
        ["formation.read"],
    ]
    assert create_client.call_args.args[5] is None
    created_hash = create_client.call_args.args[4]
    assert len(created_hash) == 64
    assert body["token"].startswith("olk_")
    assert body["webhook_secret"].startswith("olwhsec_")
    text = created.get_data(as_text=True)
    assert text.count(body["token"]) == 1
    assert text.count(body["webhook_secret"]) == 1
    assert not {"token", "token_hash", "webhook_secret"} & set(body["credential"])
    assert body["credential"]["name"] == "Integrator"
    assert "hash-created" not in listed.get_data(as_text=True)

    rotate_client.return_value = {**row, "token_hash": "hash-rotated"}
    rotated = client.post(
        "/leg/community/community-a/operator-api/credentials/client-1/rotate",
        headers=csrf,
    )
    rotated_body = rotated.get_json()
    rotated_hash = rotate_client.call_args.args[2]
    assert rotated.status_code == 200
    assert rotate_client.call_args.args[:2] == ("community-a", "client-1")
    assert len(rotated_hash) == 64
    assert rotated_hash != created_hash
    assert rotated_body["token"].startswith("olk_")
    assert rotated_body["token"] != body["token"]

    def lookup_active(token_hash):
        return (
            {**CLIENT, "token_hash": token_hash} if token_hash == rotated_hash else None
        )

    lookup.side_effect = lookup_active
    stale = client.get(
        "/api/operator/v1/communities/community-a/formation",
        headers={"Authorization": f"Bearer {body['token']}"},
    )
    fresh = client.get(
        "/api/operator/v1/communities/community-a/formation",
        headers={"Authorization": f"Bearer {rotated_body['token']}"},
    )
    assert stale.status_code == 401
    assert fresh.status_code == 200

    revoke_client.return_value = {**row, "active": False}
    revoked = client.post(
        "/leg/community/community-a/operator-api/credentials/client-1/revoke",
        headers=csrf,
    )
    assert revoked.status_code == 200
    assert revoke_client.call_args.args == ("community-a", "client-1")
    assert revoked.get_json()["credential"]["active"] is False
    lookup.side_effect = lambda token_hash: None
    rejected = client.get(
        "/api/operator/v1/communities/community-a/formation",
        headers={"Authorization": f"Bearer {rotated_body['token']}"},
    )
    assert rejected.status_code == 401


@patch("operator_api.db.record_webhook_attempt")
@patch("operator_api.db.get_pending_webhook_deliveries")
def test_dispatch_signs_canonical_body_and_headers_for_the_delivery(
    pending, record_attempt
):
    pending.return_value = [_delivery_row()]
    sent = []

    def transport(url, body, headers, timeout):
        sent.append((url, body, headers))
        return 204

    totals = operator_api.dispatch_pending_webhooks(
        transport=transport, signing_key="webhook-test-key"
    )

    assert totals == {"attempted": 1, "delivered": 1, "failed": 0}
    url, body, headers = sent[0]
    assert url == "https://hooks.example.net/ingest"
    assert headers["OpenLEG-Delivery"] == "delivery-1"
    assert (
        body
        == json.dumps(
            {
                "event_id": "event-1",
                "event_type": "formation.submitted",
                "schema_version": "operator-event/1",
                "aggregate_id": "case-1",
                "community_id": "community-a",
                "payload": {"status": "prepared"},
                "occurred_at": "2026-01-01T08:00:00+00:00",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    secret = operator_api._webhook_secret("client-1", "webhook-test-key")
    assert operator_api.verify_webhook_signature(
        body, headers["OpenLEG-Signature"], secret
    )
    record_attempt.assert_called_once_with(
        "delivery-1",
        claim_id="claim-1",
        status="delivered",
        response_status=204,
        retryable=False,
    )


@patch("operator_api.db.record_webhook_attempt")
@patch("operator_api.db.get_pending_webhook_deliveries")
def test_failing_transport_records_attempts_until_the_delivery_permanently_fails(
    pending, record_attempt
):
    pending.side_effect = [
        [_delivery_row(attempt_count=0, claim_id="claim-1")],
        [_delivery_row(attempt_count=1, claim_id="claim-2")],
        [_delivery_row(attempt_count=2, claim_id="claim-3")],
    ]
    sent = []

    def failing_transport(url, body, headers, timeout):
        sent.append(url)
        return 503

    for _ in range(3):
        totals = operator_api.dispatch_pending_webhooks(
            transport=failing_transport,
            max_attempts=3,
            signing_key="webhook-test-key",
        )

    assert sent == ["https://hooks.example.net/ingest"] * 3
    assert [call.kwargs["status"] for call in record_attempt.call_args_list] == [
        "retry",
        "retry",
        "failed",
    ]
    assert [call.kwargs["claim_id"] for call in record_attempt.call_args_list] == [
        "claim-1",
        "claim-2",
        "claim-3",
    ]
    assert all(
        call.kwargs["response_status"] == 503 for call in record_attempt.call_args_list
    )
    assert [call.kwargs["retryable"] for call in record_attempt.call_args_list] == [
        True,
        True,
        False,
    ]
    assert totals == {"attempted": 1, "delivered": 0, "failed": 1}

    pending.side_effect = [
        [_delivery_row(attempt_count=2, schema_version="operator-event/0")]
    ]
    sent.clear()
    stale = operator_api.dispatch_pending_webhooks(
        transport=failing_transport, max_attempts=3, signing_key="webhook-test-key"
    )

    assert sent == []
    assert stale == {"attempted": 1, "delivered": 0, "failed": 1}
    last = record_attempt.call_args
    assert last.args == ("delivery-1",)
    assert last.kwargs == {
        "claim_id": "claim-1",
        "status": "failed",
        "response_status": 0,
        "retryable": False,
    }


@patch("operator_api.community_access.is_administrator", return_value=True)
@patch("operator_api.formation_wizard.get_community_status")
@patch("operator_api.db.retry_operator_webhook_delivery")
def test_admin_retry_route_rearms_a_failed_delivery(
    retry_delivery, community_status, _is_admin, app
):
    app.config["SECRET_KEY"] = "credential-lifecycle-key"
    community_status.return_value = {
        "members": [{"building_id": "building-1", "status": "confirmed"}]
    }
    retry_delivery.return_value = {
        "delivery_id": "delivery-9",
        "status": "retry",
        "attempt_count": 3,
    }
    client = _register(app)
    csrf = _admin_session(client)

    response = client.post(
        "/leg/community/community-a/operator-api/deliveries/delivery-9/retry",
        headers=csrf,
    )

    assert response.status_code == 200
    assert response.get_json()["delivery"]["status"] == "retry"
    retry_delivery.assert_called_once_with("community-a", "delivery-9")

    retry_delivery.return_value = None
    missing = client.post(
        "/leg/community/community-a/operator-api/deliveries/delivery-9/retry",
        headers=csrf,
    )
    assert missing.status_code == 404


def test_webhook_signature_rejects_tampered_body_and_wrong_secret():
    body = b'{"aggregate_id":"case-1","status":"prepared"}'
    signature = operator_api.sign_webhook(body, "olwhsec_test-secret")

    assert operator_api.verify_webhook_signature(body, signature, "olwhsec_test-secret")
    assert not operator_api.verify_webhook_signature(
        b'{"aggregate_id":"case-1","status":"tampered"}',
        signature,
        "olwhsec_test-secret",
    )
    assert not operator_api.verify_webhook_signature(
        body, signature, "olwhsec_other-secret"
    )
    assert not operator_api.verify_webhook_signature(body, None, "olwhsec_test-secret")
