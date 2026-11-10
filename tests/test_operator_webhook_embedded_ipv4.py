# SPDX-License-Identifier: AGPL-3.0-or-later
"""Public webhook routing policy at credential and delivery boundaries."""

import io
import socket
from unittest.mock import MagicMock, patch

import pytest

import operator_api


@pytest.fixture
def webhook_boundary(app):
    app.register_blueprint(operator_api.operator_api_bp)
    client = app.test_client()
    with client.session_transaction() as session:
        session["dashboard_building_id"] = "building-1"
        session["dashboard_csrf_token"] = "csrf-secret"
    delivery = {
        "delivery_id": "delivery-1",
        "claim_id": "claim-1",
        "status": "processing",
        "schema_version": "operator-event/1",
        "attempt_count": 0,
        "event_id": "event-1",
        "event_type": "formation.submitted",
        "aggregate_id": "case-1",
        "community_id": "community-a",
        "payload": {},
        "occurred_at": "2026-01-01T08:00:00+00:00",
        "webhook_url": "https://hooks.example.net/ingest",
        "webhook_secret": "olwhsec_test",
        "created_by": "building-1",
    }
    with (
        patch(
            "operator_api.formation_wizard.get_community_status",
            return_value={
                "members": [
                    {
                        "building_id": "building-1",
                        "status": "confirmed",
                        "role": "admin",
                    }
                ]
            },
        ),
        patch(
            "operator_api.db.create_operator_api_client",
            return_value={"id": "client-1", "webhook_secret": "olwhsec_test"},
        ) as create,
        patch("operator_api.socket.getaddrinfo") as resolve,
        patch("operator_api.socket.create_connection") as connect,
        patch("operator_api.ssl.create_default_context") as tls,
        patch(
            "operator_api.db.get_pending_webhook_deliveries", return_value=[delivery]
        ),
        patch("operator_api.db.record_webhook_attempt") as record,
    ):
        tls.return_value.wrap_socket.return_value = MagicMock()
        tls.return_value.wrap_socket.return_value.makefile.return_value = io.BytesIO(
            b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n"
        )

        def exercise(seam, addresses, accepted):
            resolve.return_value = [
                (
                    socket.AF_INET6 if ":" in address else socket.AF_INET,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    (address, 443, 0, 0) if ":" in address else (address, 443),
                )
                for address in addresses
            ]
            if seam == "credential":
                response = client.post(
                    "/leg/community/community-a/operator-api/credentials",
                    headers={"X-CSRF-Token": "csrf-secret"},
                    json={
                        "name": "Integrator",
                        "capabilities": ["formation.read"],
                        "webhook_url": delivery["webhook_url"],
                    },
                )
                assert response.status_code == (201 if accepted else 400)
                if accepted:
                    create.assert_called_once()
                else:
                    assert response.get_json()["error"] == (
                        "Webhook host must be publicly routable"
                    )
                    create.assert_not_called()
                connect.assert_not_called()
            else:
                totals = operator_api.dispatch_pending_webhooks()
                assert totals == {
                    "attempted": 1,
                    "delivered": int(accepted),
                    "failed": int(not accepted),
                }
                record.assert_called_once_with(
                    "delivery-1",
                    claim_id="claim-1",
                    status="delivered" if accepted else "retry",
                    response_status=204 if accepted else 0,
                    retryable=not accepted,
                )
                if accepted:
                    connect.assert_called_once_with((addresses[0], 443), 10, None)
                else:
                    connect.assert_not_called()
            resolve.assert_called_once_with(
                "hooks.example.net", 443, type=socket.SOCK_STREAM
            )

        yield exercise


@pytest.mark.parametrize("seam", ["credential", "dispatch"])
@pytest.mark.parametrize("address", ["64:ff9b::a00:1", "64:ff9b::7f00:1"])
def test_nat64_private_destination_is_rejected(webhook_boundary, seam, address):
    webhook_boundary(seam, [address], False)


@pytest.mark.parametrize("seam", ["credential", "dispatch"])
@pytest.mark.parametrize("address", ["::127.0.0.1", "::10.0.0.1", "::100.64.0.1"])
def test_compatible_nonpublic_destination_is_rejected(webhook_boundary, seam, address):
    webhook_boundary(seam, [address], False)


@pytest.mark.parametrize("seam", ["credential", "dispatch"])
@pytest.mark.parametrize("address", ["fec0::1", "feff::1"])
def test_site_local_destination_is_rejected(webhook_boundary, seam, address):
    webhook_boundary(seam, [address], False)


@pytest.mark.parametrize("seam", ["credential", "dispatch"])
@pytest.mark.parametrize(
    "address", ["::ffff:10.0.0.1", "::ffff:127.0.0.1", "::ffff:100.64.0.1"]
)
def test_mapped_nonpublic_destination_is_rejected(webhook_boundary, seam, address):
    webhook_boundary(seam, [address], False)


@pytest.mark.parametrize("seam", ["credential", "dispatch"])
@pytest.mark.parametrize(
    "address, accepted",
    [
        ("2002:5db8:d70e::1", True),
        ("2002:a00:1::1", False),
        ("2002:7f00:1::1", False),
        ("2002:6440:1::1", False),
    ],
)
def test_6to4_uses_embedded_ipv4(webhook_boundary, seam, address, accepted):
    webhook_boundary(seam, [address], accepted)


@pytest.mark.parametrize("seam", ["credential", "dispatch"])
@pytest.mark.parametrize(
    "address, accepted",
    [
        # Server and complemented client both encode 93.184.215.14.
        ("2001:0:5db8:d70e:0:ffff:a247:28f1", True),
        ("2001:0:5db8:d70e:0:ffff:f5ff:fffe", False),  # client 10.0.0.1
        ("2001:0:a00:1:0:ffff:a247:28f1", False),  # server 10.0.0.1
        ("2001:0:5db8:d70e:0:ffff:80ff:fffe", False),  # client 127.0.0.1
        ("2001:0:7f00:1:0:ffff:a247:28f1", False),  # server 127.0.0.1
        ("2001:0:5db8:d70e:0:ffff:9bbf:fffe", False),  # client 100.64.0.1
        ("2001:0:6440:1:0:ffff:a247:28f1", False),  # server 100.64.0.1
    ],
)
def test_teredo_requires_global_server_and_client(
    webhook_boundary, seam, address, accepted
):
    webhook_boundary(seam, [address], accepted)


@pytest.mark.parametrize("seam", ["credential", "dispatch"])
@pytest.mark.parametrize(
    "address",
    [
        "::ffff:93.184.215.14",
        "::93.184.215.14",
        "64:ff9b::5db8:d70e",
        "2606:4700::1111",
        "93.184.215.14",
    ],
)
def test_global_destinations_remain_accepted(webhook_boundary, seam, address):
    webhook_boundary(seam, [address], True)


@pytest.mark.parametrize("seam", ["credential", "dispatch"])
@pytest.mark.parametrize(
    "address",
    [
        "64:ff9b:1::a00:1",
        "64:ff9b:1::5db8:d70e",
        "64:ff9b:1:ffff:ffff:ffff:ffff:ffff",
        "10.0.0.1",
        "127.0.0.1",
        "100.64.0.1",
        "::",
        "::1",
        "fe80::1",
        "fc00::1",
    ],
)
def test_existing_nonpublic_destinations_remain_rejected(
    webhook_boundary, seam, address
):
    webhook_boundary(seam, [address], False)


@pytest.mark.parametrize("seam", ["credential", "dispatch"])
@pytest.mark.parametrize("unsafe_first", [True, False])
def test_one_nonpublic_answer_rejects_the_entire_host(
    webhook_boundary, seam, unsafe_first
):
    addresses = ["64:ff9b::a00:1", "2606:4700::1111"]
    if not unsafe_first:
        addresses.reverse()
    webhook_boundary(seam, addresses, False)
