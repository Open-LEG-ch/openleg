# SPDX-License-Identifier: AGPL-3.0-or-later
"""Signed delivery metadata at the public dispatch boundary."""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from unittest.mock import patch

import operator_api

SECRET = "olwhsec_metadata-test"
EVENT = {
    "event_id": "event-1",
    "event_type": "formation.submitted",
    "schema_version": "operator-event/1",
    "aggregate_id": "case-1",
    "community_id": "community-a",
    "payload": {"status": "prepared", "label": "Zürich"},
    "occurred_at": "2026-01-01T08:00:00+00:00",
}


def delivery():
    return {
        **EVENT,
        "delivery_id": "delivery-1",
        "claim_id": "claim-1",
        "status": "processing",
        "attempt_count": 0,
        "webhook_url": "https://hooks.example.net/ingest",
        "webhook_secret": SECRET,
    }


def assert_signature(body, headers):
    expected = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(headers["OpenLEG-Signature"], expected)


@patch("operator_api.db.record_webhook_attempt")
@patch("operator_api.db.get_pending_webhook_deliveries")
def test_signed_delivery_id_matches_header(pending, record):
    pending.return_value = [delivery()]
    sent = []

    def transport(url, body, headers, timeout):
        sent.append((body, headers))
        return 204

    operator_api.dispatch_pending_webhooks(transport=transport)

    body, headers = sent[0]
    payload = json.loads(body)
    assert payload["delivery_id"] == headers["OpenLEG-Delivery"] == "delivery-1"
    assert {key: payload[key] for key in EVENT} == EVENT
    assert_signature(body, headers)


@patch("operator_api.db.record_webhook_attempt")
@patch("operator_api.db.get_pending_webhook_deliveries")
def test_retry_refreshes_signed_attempt_time_and_preserves_event(pending, record):
    times = iter(
        [
            datetime(2026, 11, 11, 12, 0, 0, 123456, tzinfo=UTC),
            datetime(2026, 11, 11, 12, 0, 30, 654321, tzinfo=UTC),
        ]
    )

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return next(times).astimezone(tz)

    pending.side_effect = [
        [delivery()],
        [{**delivery(), "attempt_count": 1, "claim_id": "claim-2"}],
    ]
    sent = []

    def transport(url, body, headers, timeout):
        sent.append((body, headers))
        return 503 if len(sent) == 1 else 204

    with patch("operator_api.datetime", Clock):
        operator_api.dispatch_pending_webhooks(transport=transport)
        operator_api.dispatch_pending_webhooks(transport=transport)

    for (body, headers), sent_at in zip(
        sent,
        ["2026-11-11T12:00:00.123456+00:00", "2026-11-11T12:00:30.654321+00:00"],
        strict=True,
    ):
        expected = {**EVENT, "delivery_id": "delivery-1", "sent_at": sent_at}
        assert json.loads(body) == expected
        assert (
            body == json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()
        )
        assert headers["OpenLEG-Delivery"] == "delivery-1"
        assert_signature(body, headers)
    assert sent[0][1]["OpenLEG-Signature"] != sent[1][1]["OpenLEG-Signature"]
    assert record.call_args_list[0].kwargs["status"] == "retry"
    assert record.call_args_list[1].kwargs["status"] == "delivered"
