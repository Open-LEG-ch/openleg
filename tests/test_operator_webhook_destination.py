# SPDX-License-Identifier: AGPL-3.0-or-later
"""Webhook destinations must stay on the public internet.

An administrator enters a webhook URL and OpenLEG posts signed events to it
from inside the hosting network. Both the credential route and every dispatch
must refuse destinations that resolve to loopback, private, link-local or
otherwise non-public addresses, and dispatch must connect to the address it
validated, not re-resolve the hostname.
"""

import socket
from unittest.mock import patch

import pytest

import operator_api

COMMUNITY = "community-a"
PUBLIC_IP = "93.184.215.14"
CREATE_PATH = f"/leg/community/{COMMUNITY}/operator-api/credentials"


def _resolves_to(*addresses):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (
                socket.AF_INET6 if ":" in address else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (address, port),
            )
            for address in addresses
        ]

    return fake_getaddrinfo


def _admin_client(app):
    if "operator_api" not in app.blueprints:
        app.register_blueprint(operator_api.operator_api_bp)
    app.config["SECRET_KEY"] = "webhook-destination-key"
    client = app.test_client()
    with client.session_transaction() as session:
        session["dashboard_building_id"] = "building-1"
        session["dashboard_csrf_token"] = "csrf-secret"
    return client


NON_PUBLIC = [
    ("127.0.0.1",),
    ("10.0.0.5",),
    ("172.16.3.4",),
    ("192.168.1.10",),
    ("169.254.169.254",),
    ("100.64.0.1",),
    ("0.0.0.0",),
    ("::1",),
    ("fd00::1",),
    ("fe80::1",),
    ("::ffff:127.0.0.1",),
    (PUBLIC_IP, "10.0.0.5"),
]


@pytest.mark.parametrize("addresses", NON_PUBLIC, ids=lambda a: "+".join(a))
@patch("operator_api.db.create_operator_api_client")
@patch(
    "operator_api.formation_wizard.get_community_status",
    return_value={
        "members": [
            {"building_id": "building-1", "status": "confirmed", "role": "admin"}
        ]
    },
)
def test_credential_route_refuses_a_webhook_that_resolves_to_a_non_public_address(
    _status, create_client, app, addresses
):
    client = _admin_client(app)
    with patch("operator_api.socket.getaddrinfo", _resolves_to(*addresses)):
        response = client.post(
            CREATE_PATH,
            headers={"X-CSRF-Token": "csrf-secret"},
            json={
                "name": "Integrator",
                "capabilities": ["formation.read"],
                "actor_kind": "service",
                "webhook_url": "https://hooks.example.net/ingest",
            },
        )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Webhook host must be publicly routable"
    assert not create_client.called


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.net/ingest",
        "ftp://hooks.example.net/ingest",
        "https:///ingest",
    ],
)
@patch("operator_api.db.create_operator_api_client")
@patch(
    "operator_api.formation_wizard.get_community_status",
    return_value={
        "members": [
            {"building_id": "building-1", "status": "confirmed", "role": "admin"}
        ]
    },
)
def test_credential_route_refuses_a_webhook_without_https(
    _status, create_client, app, url
):
    client = _admin_client(app)
    with patch("operator_api.socket.getaddrinfo", _resolves_to(PUBLIC_IP)):
        response = client.post(
            CREATE_PATH,
            headers={"X-CSRF-Token": "csrf-secret"},
            json={
                "name": "Integrator",
                "capabilities": ["formation.read"],
                "actor_kind": "service",
                "webhook_url": url,
            },
        )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Webhook URL must use HTTPS"
    assert not create_client.called


def _delivery(**overrides):
    row = {
        "delivery_id": "delivery-1",
        "claim_id": "claim-1",
        "status": "processing",
        "schema_version": "operator-event/1",
        "attempt_count": 0,
        "event_id": "event-1",
        "event_type": "formation.submission.prepared",
        "aggregate_id": "case-1",
        "community_id": COMMUNITY,
        "payload": {"state": "prepared"},
        "occurred_at": "2026-01-01T08:00:00+00:00",
        "webhook_url": "https://hooks.example.net/ingest",
        "webhook_secret": "olwhsec_test-delivery-secret",
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize("addresses", NON_PUBLIC, ids=lambda a: "+".join(a))
@patch("operator_api.db.record_webhook_attempt")
@patch("operator_api.db.get_pending_webhook_deliveries")
def test_dispatch_never_connects_when_the_host_now_resolves_to_a_non_public_address(
    pending, record_attempt, addresses
):
    pending.return_value = [_delivery()]
    with (
        patch("operator_api.socket.getaddrinfo", _resolves_to(*addresses)),
        patch("operator_api.socket.create_connection") as connect,
    ):
        totals = operator_api.dispatch_pending_webhooks(signing_key="k")

    assert not connect.called
    assert totals == {"attempted": 1, "delivered": 0, "failed": 1}
    assert record_attempt.call_args.kwargs["status"] == "retry"
    assert record_attempt.call_args.kwargs["response_status"] == 0


@patch("operator_api.db.record_webhook_attempt")
@patch("operator_api.db.get_pending_webhook_deliveries")
def test_dispatch_connects_to_the_validated_address_not_the_hostname(
    pending, record_attempt
):
    pending.return_value = [_delivery()]
    with (
        patch("operator_api.socket.getaddrinfo", _resolves_to(PUBLIC_IP)),
        patch(
            "operator_api.socket.create_connection", side_effect=OSError("stop")
        ) as connect,
    ):
        operator_api.dispatch_pending_webhooks(signing_key="k")

    assert connect.call_count == 1
    assert connect.call_args.args[0] == (PUBLIC_IP, 443)
