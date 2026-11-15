# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cron releases preserve attempts and can be claimed by a later run."""

from unittest.mock import patch

import pytest
from flask import Flask

import cron
from tests import test_interest_postgres

interest_database = test_interest_postgres.interest_database
pytestmark = pytest.mark.integration


def test_cron_releases_without_attempt_and_next_run_can_deliver(interest_database):
    db = interest_database
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES ('a','Fictional LEG')"
        )
    db.create_operator_api_client(
        "a", "owner", "Receiver", ["formation.read"], "hash", "https://example.net/hook"
    )
    for i in range(2):
        db.create_operator_event("formation.submitted", str(i), "a", {})
    app = Flask(__name__)
    app.config["CRON_SECRET"] = "test-secret"
    app.register_blueprint(cron.cron_bp)
    with (
        patch("operator_api._creator_authority", return_value=True),
        patch("operator_api._default_transport", side_effect=TimeoutError),
    ):
        response = app.test_client().post(
            "/api/cron/process-operator-webhooks",
            headers={"X-Cron-Secret": "test-secret"},
        )
    assert response.status_code == 200
    assert response.json == {"attempted": 1, "delivered": 0, "failed": 1}
    deliveries = db.list_operator_webhook_deliveries("a")
    assert sorted(row["attempt_count"] for row in deliveries) == [0, 1]
    assert all(row["status"] == "retry" for row in deliveries)
    released = next(row for row in deliveries if row["attempt_count"] == 0)
    assert released["last_attempt_at"] is None
    assert released["response_status"] is None
    with (
        patch("operator_api._creator_authority", return_value=True),
        patch("operator_api._default_transport", return_value=204) as transport,
    ):
        response = app.test_client().post(
            "/api/cron/process-operator-webhooks",
            headers={"X-Cron-Secret": "test-secret"},
        )
    assert response.json == {"attempted": 1, "delivered": 1, "failed": 0}
    assert transport.call_args.args[2]["OpenLEG-Delivery"] == released["delivery_id"]
    delivered = next(
        row
        for row in db.list_operator_webhook_deliveries("a")
        if row["delivery_id"] == released["delivery_id"]
    )
    assert delivered["status"] == "delivered"
    assert delivered["attempt_count"] == 1
