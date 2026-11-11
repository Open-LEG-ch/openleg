# SPDX-License-Identifier: AGPL-3.0-or-later
"""Operator event signatures are independent of the browser session key."""

import hashlib

import pytest

import operator_api
from tests import security_review_fixtures
from tests import test_interest_postgres as interest_postgres_tests
from tests.test_dashboard_access_routes import _set_session

app_module = security_review_fixtures.app_module
interest_database = interest_postgres_tests.interest_database

pytestmark = pytest.mark.integration


def test_issued_secret_is_persisted_and_signs_after_session_key_rotation(
    app_module, interest_database
):
    db = interest_database
    assert db.save_building(
        "owner",
        "owner@example.ch",
        {"address": "Testweg 1", "lat": 47.2, "lon": 8.2},
        {},
    )
    cid = db.create_community_record("Synthetic", "owner", "simple", "")
    client = app_module.web.test_client()
    _set_session(client, "owner")
    response = client.post(
        f"/leg/community/{cid}/operator-api/credentials",
        json={
            "name": "Synthetic operator",
            "capabilities": ["formation.read"],
            "actor_kind": "service",
        },
        headers={"X-CSRF-Token": "csrf-secret"},
    )
    assert response.status_code == 201
    issued = response.json
    row = db.get_operator_api_client_by_token_hash(
        hashlib.sha256(issued["token"].encode()).hexdigest()
    )
    assert row.get("webhook_secret") == issued["webhook_secret"]
    assert issued["webhook_secret"] != operator_api._webhook_secret(
        row["id"], app_module.web.config["SECRET_KEY"]
    )
    second = client.post(
        f"/leg/community/{cid}/operator-api/credentials",
        json={
            "name": "Second operator",
            "capabilities": ["formation.read"],
            "actor_kind": "service",
        },
        headers={"X-CSRF-Token": "csrf-secret"},
    )
    assert second.status_code == 201
    assert second.json["webhook_secret"] != issued["webhook_secret"]
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE operator_api_clients SET webhook_url='https://hooks.example.invalid/events' WHERE id=%s",
            (row["id"],),
        )
    sent = []

    def deliver(url, body, headers, timeout):
        sent.append((body, headers["OpenLEG-Signature"]))
        return 204

    for index, key in enumerate(("first-session-key", "replacement-session-key")):
        app_module.web.config["SECRET_KEY"] = key
        db.create_operator_event(
            "formation.submitted", f"case-{index}", cid, {"state": "submitted"}
        )
        with app_module.web.app_context():
            assert (
                operator_api.dispatch_pending_webhooks(transport=deliver)["delivered"]
                == 1
            )
        assert operator_api.verify_webhook_signature(
            *sent[-1], issued["webhook_secret"]
        )

    _set_session(client, "owner")
    rotated = client.post(
        f"/leg/community/{cid}/operator-api/credentials/{row['id']}/rotate",
        headers={"X-CSRF-Token": "csrf-secret"},
    )
    assert rotated.status_code == 200
    assert rotated.json["webhook_secret"] != issued["webhook_secret"]
    current = db.get_operator_api_client_by_token_hash(
        hashlib.sha256(rotated.json["token"].encode()).hexdigest()
    )
    assert current["webhook_secret"] == rotated.json["webhook_secret"]
    rotated_again = client.post(
        f"/leg/community/{cid}/operator-api/credentials/{row['id']}/rotate",
        headers={"X-CSRF-Token": "csrf-secret"},
    )
    assert rotated_again.status_code == 200
    assert rotated_again.json["webhook_secret"] != rotated.json["webhook_secret"]

    # A credential created before this migration cannot silently acquire a new
    # receiver key. Its owner must rotate it and install the returned secret.
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE operator_api_clients SET webhook_secret=NULL WHERE id=%s",
            (row["id"],),
        )
    listed = client.get(
        f"/leg/community/{cid}/operator-api/credentials",
        headers={"X-CSRF-Token": "csrf-secret"},
    )
    credential = next(
        item for item in listed.json["credentials"] if item["id"] == row["id"]
    )
    assert credential["webhook_requires_rotation"] is True
    before = len(sent)
    db.create_operator_event("formation.submitted", "legacy-case", cid, {})
    with app_module.web.app_context():
        assert operator_api.dispatch_pending_webhooks(transport=deliver)["failed"] == 1
    assert len(sent) == before
