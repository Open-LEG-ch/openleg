# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dispatch deadlines and fair progress across webhook credentials."""

from unittest.mock import patch

import pytest

import operator_api
from tests.test_operator_api import _delivery_row


def test_timeout_releases_same_client_and_delivers_other_community():
    rows = [
        _delivery_row(delivery_id="slow-1"),
        _delivery_row(delivery_id="slow-2", attempt_count=2),
        _delivery_row(delivery_id="fast", client_id="other", community_id="other"),
    ]
    sent = []

    def transport(url, body, headers, timeout):
        sent.append(headers["OpenLEG-Delivery"])
        if len(sent) == 1:
            raise TimeoutError("deadline")
        return 204

    with (
        patch("operator_api.db.get_pending_webhook_deliveries", return_value=rows),
        patch("operator_api._creator_authority", return_value=True),
        patch("operator_api.db.record_webhook_attempt") as record,
        patch("operator_api.db.release_webhook_delivery", create=True) as release,
    ):
        totals = operator_api.dispatch_pending_webhooks(transport=transport)
    assert sent == ["slow-1", "fast"]
    assert totals == {"attempted": 2, "delivered": 1, "failed": 1}
    release.assert_called_once_with("slow-2", claim_id="claim-1")
    assert record.call_args_list[0].kwargs == {
        "claim_id": "claim-1",
        "status": "retry",
        "response_status": 0,
        "retryable": True,
    }
    assert [call.args[0] for call in record.call_args_list] == ["slow-1", "fast"]
    assert rows[1]["attempt_count"] == 2


def test_run_budget_releases_unreached_deliveries():
    now = [0.0]
    rows = [_delivery_row(delivery_id=str(i), client_id=str(i)) for i in range(10)]
    timeouts = []

    def transport(url, body, headers, timeout):
        timeouts.append(timeout)
        now[0] += timeout
        raise TimeoutError("deadline")

    with (
        patch("operator_api.db.get_pending_webhook_deliveries", return_value=rows),
        patch("operator_api._creator_authority", return_value=True),
        patch("operator_api.db.record_webhook_attempt") as record,
        patch("operator_api.db.release_webhook_delivery") as release,
    ):
        totals = operator_api.dispatch_pending_webhooks(
            transport=transport, clock=lambda: now[0]
        )
    assert timeouts == [15] * 8
    assert totals == {"attempted": 8, "delivered": 0, "failed": 8}
    assert record.call_count == 8
    assert [call.args[0] for call in release.call_args_list] == ["8", "9"]


@pytest.mark.parametrize(
    "prefix", [b"HTTP/1.1 204 ", b"HTTP/1.1 204 No Content\r\nX-Trickle: "]
)
def test_default_transport_stops_trickling_headers_and_closes_connection(prefix):
    import socket
    import threading
    import time

    client, server = socket.socketpair()
    stopped = threading.Event()

    def receiver():
        try:
            server.recv(65536)
            server.sendall(prefix)
            for _ in range(40):
                if stopped.wait(0.02):
                    return
                server.sendall(b"x")
            server.sendall(b"\r\nContent-Length: 0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            server.close()

    worker = threading.Thread(target=receiver)
    worker.start()
    try:
        with (
            patch(
                "operator_api.db.get_pending_webhook_deliveries",
                return_value=[_delivery_row()],
            ),
            patch("operator_api._creator_authority", return_value=True),
            patch("operator_api.db.record_webhook_attempt") as record,
            patch(
                "operator_api.socket.getaddrinfo",
                return_value=[(2, 1, 6, "", ("8.8.8.8", 443))],
            ),
            patch("operator_api.socket.create_connection", return_value=client),
            patch("operator_api.ssl.create_default_context") as context,
        ):
            context.return_value.wrap_socket.return_value = client
            ticks = iter([0, 119.8, 119.8])
            started = time.monotonic()
            totals = operator_api.dispatch_pending_webhooks(clock=lambda: next(ticks))
            elapsed = time.monotonic() - started
        assert totals == {"attempted": 1, "delivered": 0, "failed": 1}
        assert record.call_args.kwargs["response_status"] == 0
        assert record.call_args.kwargs["retryable"] is True
        assert elapsed < 0.6
        assert client.fileno() == -1
    finally:
        stopped.set()
        client.close()
        worker.join(timeout=2)
        assert not worker.is_alive()


def test_connect_and_tls_share_the_response_deadline():
    import socket
    from types import SimpleNamespace

    client, server = socket.socketpair()
    now = [0.0]
    budgets = []

    def connect(*args):
        budgets.append(args[1])
        now[0] += 9
        return client

    def handshake(raw, **kwargs):
        budgets.append(raw.gettimeout())
        now[0] += 7
        return raw

    try:
        server.sendall(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
        with (
            patch(
                "operator_api.db.get_pending_webhook_deliveries",
                return_value=[_delivery_row()],
            ),
            patch("operator_api._creator_authority", return_value=True),
            patch("operator_api.db.record_webhook_attempt") as record,
            patch("operator_api.time", SimpleNamespace(monotonic=lambda: now[0])),
            patch(
                "operator_api.socket.getaddrinfo",
                return_value=[(2, 1, 6, "", ("8.8.8.8", 443))],
            ),
            patch("operator_api.socket.create_connection", side_effect=connect),
            patch("operator_api.ssl.create_default_context") as context,
        ):
            context.return_value.wrap_socket.side_effect = handshake
            totals = operator_api.dispatch_pending_webhooks()
        assert budgets == [15, 6]
        assert totals == {"attempted": 1, "delivered": 0, "failed": 1}
        assert record.call_args.kwargs["response_status"] == 0
        assert client.fileno() == -1
    finally:
        client.close()
        server.close()
