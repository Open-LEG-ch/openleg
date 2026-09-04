# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical AgentMail event outcomes (issue #489).

One canonical interface owns the AgentMail webhook payload variants:
field precedence between the message object and the event envelope,
recipient normalization, the bounded safe preview, and the caps. These
tests assert the canonical outcomes and that the verified webhook route
persists exactly the canonical event — not helper calls or implementation
spelling.
"""

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agentmail_event import build_event

STORED_FIELDS = {
    "event_type",
    "event_id",
    "inbox_id",
    "message_id",
    "thread_id",
    "from_email",
    "from_name",
    "to",
    "subject",
    "received_at",
    "text_preview",
}


class TestCanonicalShape:
    def test_the_stored_field_names_are_stable(self):
        assert set(build_event({})) == STORED_FIELDS
        assert set(build_event("garbage")) == STORED_FIELDS


class TestFieldPrecedence:
    def test_event_type_prefers_event_type_then_type_then_event(self):
        assert (
            build_event({"event_type": "a", "type": "b", "event": "c"})["event_type"]
            == "a"
        )
        assert build_event({"type": "b", "event": "c"})["event_type"] == "b"
        assert build_event({"event": "c"})["event_type"] == "c"
        assert build_event({})["event_type"] == "unknown"

    def test_message_id_prefers_message_fields_then_the_envelope(self):
        assert (
            build_event(
                {"message_id": "env", "message": {"message_id": "m", "id": "i"}}
            )["message_id"]
            == "m"
        )
        assert (
            build_event({"message_id": "env", "message": {"id": "i"}})["message_id"]
            == "i"
        )
        assert build_event({"message_id": "env"})["message_id"] == "env"

    def test_thread_id_falls_back_to_the_envelope(self):
        assert (
            build_event({"thread_id": "env", "message": {"thread_id": "m"}})[
                "thread_id"
            ]
            == "m"
        )
        assert build_event({"thread_id": "env"})["thread_id"] == "env"

    def test_received_at_walks_message_then_envelope_variants(self):
        envelope = {"received_at": "env_r", "timestamp": "env_t"}
        assert (
            build_event(
                {**envelope, "message": {"received_at": "m_r", "timestamp": "m_t"}}
            )["received_at"]
            == "m_r"
        )
        assert (
            build_event({**envelope, "message": {"timestamp": "m_t"}})["received_at"]
            == "m_t"
        )
        assert build_event(envelope)["received_at"] == "env_r"
        assert build_event({"timestamp": "env_t"})["received_at"] == "env_t"

    def test_subject_and_sender_fall_back_to_headers(self):
        event = build_event(
            {"message": {"headers": {"subject": "Betreff", "from": "abs@example.ch"}}}
        )
        assert event["subject"] == "Betreff"
        assert event["from_email"] == "abs@example.ch"

    def test_message_fields_win_over_headers(self):
        event = build_event(
            {
                "message": {
                    "subject": "S",
                    "from": {"email": "a@example.ch", "name": "A"},
                    "headers": {"subject": "H", "from": "h@example.ch"},
                }
            }
        )
        assert event["subject"] == "S"
        assert event["from_email"] == "a@example.ch"
        assert event["from_name"] == "A"

    def test_sender_accepts_list_string_and_from_underscore_variants(self):
        assert build_event({"message": {"from": ["l@example.ch"]}})["from_email"] == (
            "l@example.ch"
        )
        assert build_event({"message": {"from": "s@example.ch"}})["from_email"] == (
            "s@example.ch"
        )
        assert (
            build_event(
                {"message": {"from_": [{"email": "u@example.ch", "name": "U"}]}}
            )["from_email"]
            == "u@example.ch"
        )


class TestMalformedFieldsStaySafe:
    def test_a_non_dict_message_degrades_to_envelope_fields(self):
        event = build_event(
            {
                "message": "not-a-dict",
                "type": "message.received",
                "message_id": "env",
                "text_preview": "envelope preview",
            }
        )
        assert event["event_type"] == "message.received"
        assert event["message_id"] == "env"
        assert event["to"] == []
        assert event["subject"] == ""
        assert event["text_preview"] == "envelope preview"

    def test_non_dict_headers_are_ignored(self):
        assert (
            build_event({"message": {"headers": ["Subject: x"], "subject": "S"}})[
                "subject"
            ]
            == "S"
        )
        assert build_event({"message": {"headers": "oops"}})["subject"] == ""

    def test_a_non_dict_payload_degrades_to_safe_defaults(self):
        event = build_event("garbage")
        assert event["event_type"] == "unknown"
        assert event["to"] == []
        assert event["text_preview"] == ""

    def test_malformed_sender_and_recipients_do_not_raise(self):
        event = build_event({"message": {"from": 123, "to": 0}})
        assert event["from_email"] == ""
        assert event["to"] == []
        assert build_event({"message": {"to": 3}})["to"] == []


class TestRecipientNormalization:
    def test_strings_and_dicts_normalize_to_email_name_pairs(self):
        event = build_event(
            {
                "message": {
                    "to": ["a@example.ch", {"email": "c@example.ch", "name": "C"}]
                }
            }
        )
        assert event["to"] == [
            {"email": "a@example.ch", "name": ""},
            {"email": "c@example.ch", "name": "C"},
        ]

    def test_a_single_dict_recipient_is_wrapped(self):
        assert build_event({"message": {"to": {"email": "a@example.ch"}}})["to"] == [
            {"email": "a@example.ch", "name": ""}
        ]

    def test_a_bare_string_recipient_is_one_recipient(self):
        assert build_event({"message": {"to": "a@example.ch"}})["to"] == [
            {"email": "a@example.ch", "name": ""}
        ]

    def test_non_identity_entries_are_dropped(self):
        event = build_event({"message": {"to": [None, 7, ["x"], "a@example.ch"]}})
        assert event["to"] == [{"email": "a@example.ch", "name": ""}]

    def test_recipients_are_capped_at_five(self):
        event = build_event({"message": {"to": [f"r{i}@example.ch" for i in range(7)]}})
        assert event["to"] == [
            {"email": f"r{i}@example.ch", "name": ""} for i in range(5)
        ]

    def test_a_missing_recipient_list_is_empty(self):
        assert build_event({"message": {}})["to"] == []


class TestPreviewCaps:
    def test_preview_preference_order(self):
        assert (
            build_event(
                {
                    "message": {
                        "text_preview": "tp",
                        "extracted_text": "et",
                        "snippet": "sn",
                        "text": "tx",
                    }
                }
            )["text_preview"]
            == "tp"
        )
        assert (
            build_event(
                {"message": {"extracted_text": "et", "snippet": "sn", "text": "tx"}}
            )["text_preview"]
            == "et"
        )
        assert (
            build_event({"message": {"snippet": "sn", "text": "tx"}})["text_preview"]
            == "sn"
        )
        assert build_event({"message": {"text": "tx"}})["text_preview"] == "tx"
        assert (
            build_event({"text_preview": "env", "message": {}})["text_preview"] == "env"
        )
        assert build_event({"message": {}})["text_preview"] == ""

    def test_preview_is_capped_at_280_characters(self):
        event = build_event({"message": {"text": "x" * 500}})
        assert event["text_preview"] == "x" * 280

    def test_a_non_string_preview_is_coerced_and_bounded(self):
        assert build_event({"message": {"text": 12345}})["text_preview"] == "12345"
        long_value = {"key": "v" * 500}
        preview = build_event({"message": {"text": long_value}})["text_preview"]
        assert preview == str(long_value)[:280]

    def test_a_falsy_preview_falls_through_to_the_next_variant(self):
        assert (
            build_event({"message": {"text_preview": "", "snippet": "sn"}})[
                "text_preview"
            ]
            == "sn"
        )


ROUTE_PAYLOAD = {
    "type": "message.received",
    "event_id": "evt_1",
    "message_id": "msg_envelope",
    "message": {
        "id": "msg_1",
        "thread_id": "thd_1",
        "inbox_id": "hallo@openleg.ch",
        "from_": {"email": "sender@example.com", "name": "Sender"},
        "to": [
            "hallo@openleg.ch",
            {"email": "two@example.com", "name": "Two"},
        ],
        "timestamp": "2026-09-04T10:00:00Z",
        "headers": {"subject": "LEG Anfrage"},
        "text": "Guten Tag",
    },
}


def _load_app():
    with (
        patch("database.is_db_available", return_value=True),
        patch("database._connection_pool", MagicMock()),
    ):
        import app as app_module

        return SimpleNamespace(
            app=app_module.create_app(load_environment=False), db=app_module.db
        )


@pytest.fixture
def app_with_internal_token():
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": "postgresql://x:x@localhost/x",
            "REDIS_URL": "memory://",
            "APP_BASE_URL": "http://localhost:5003",
            "ADMIN_TOKEN": "admin-token",
            "INTERNAL_TOKEN": "internal-token",
            "AGENTMAIL_WEBHOOK_SECRET": "",
        },
    ):
        yield _load_app()


AGENTMAIL_TEST_SECRET = "whsec_" + "A" * 43 + "="


@pytest.fixture
def app_with_agentmail_secret():
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": "postgresql://x:x@localhost/x",
            "REDIS_URL": "memory://",
            "APP_BASE_URL": "http://localhost:5003",
            "ADMIN_TOKEN": "admin-token",
            "INTERNAL_TOKEN": "internal-token",
            "AGENTMAIL_WEBHOOK_SECRET": AGENTMAIL_TEST_SECRET,
        },
    ):
        yield _load_app()


class TestVerifiedWebhookPersistsTheCanonicalEvent:
    def test_the_verified_route_persists_the_canonical_event(
        self, app_with_internal_token
    ):
        module = app_with_internal_token
        with patch.object(module.db, "save_ops_snapshot", return_value=True) as saved:
            response = module.app.test_client().post(
                "/api/internal/agentmail",
                json=ROUTE_PAYLOAD,
                headers={"X-Internal-Token": "internal-token"},
            )

        assert response.status_code == 200
        assert response.get_json() == {"ok": True}
        saved.assert_called_once()
        kwargs = saved.call_args.kwargs
        assert kwargs["source"] == "agentmail"
        assert kwargs["category"] == "lea_inbox"
        assert kwargs["status"] == "received"
        assert kwargs["summary_text"] == "LEG Anfrage"
        assert kwargs["payload"] == build_event(ROUTE_PAYLOAD)
        assert set(kwargs["payload"]) == STORED_FIELDS

    def test_a_signed_route_request_persists_the_canonical_event(
        self, app_with_agentmail_secret
    ):
        import datetime

        from svix.webhooks import Webhook

        module = app_with_agentmail_secret
        body = json.dumps(ROUTE_PAYLOAD).encode()
        timestamp = datetime.datetime.now(datetime.timezone.utc)
        signature = Webhook(AGENTMAIL_TEST_SECRET).sign(
            "msg_test", timestamp, body.decode()
        )
        with patch.object(module.db, "save_ops_snapshot", return_value=True) as saved:
            response = module.app.test_client().post(
                "/api/internal/agentmail",
                data=body,
                content_type="application/json",
                headers={
                    "svix-id": "msg_test",
                    "svix-timestamp": str(int(timestamp.timestamp())),
                    "svix-signature": signature,
                },
            )

        assert response.status_code == 200
        assert response.get_json() == {"ok": True}
        saved.assert_called_once()
        assert saved.call_args.kwargs["payload"] == build_event(ROUTE_PAYLOAD)

    def test_a_non_inbound_event_is_still_ignored_without_persistence(
        self, app_with_internal_token
    ):
        module = app_with_internal_token
        with patch.object(module.db, "save_ops_snapshot", return_value=True) as saved:
            response = module.app.test_client().post(
                "/api/internal/agentmail",
                json={"type": "message.sent"},
                headers={"X-Internal-Token": "internal-token"},
            )

        assert response.status_code == 200
        assert response.get_json() == {"ok": True, "ignored": True}
        saved.assert_not_called()
