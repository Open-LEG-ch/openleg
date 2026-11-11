# SPDX-License-Identifier: AGPL-3.0-or-later
"""A failed webhook delivery that an administrator re-arms is delivered again."""

from unittest.mock import patch

import pytest
from flask import Flask

import operator_api
from tests import test_interest_postgres

interest_database = test_interest_postgres.interest_database

COMMUNITY = "community-retry"
DELIVERIES = f"/leg/community/{COMMUNITY}/operator-api/deliveries"


def _admin_client():
    # The shared ``app`` fixture swaps ``database`` for a mock; this test needs
    # the real store, so it mounts the operator blueprint on its own app.
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="webhook-retry-key")
    app.register_blueprint(operator_api.operator_api_bp)
    client = app.test_client()
    with client.session_transaction() as session:
        session["dashboard_building_id"] = "building-1"
        session["dashboard_csrf_token"] = "csrf-secret"
    return client


def _make_due(db):
    """Let the backoff elapse without sleeping in the test."""
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE operator_webhook_deliveries SET next_attempt_at=CURRENT_TIMESTAMP"
        )


@pytest.mark.integration
def test_an_exhausted_delivery_rearmed_by_an_administrator_is_delivered(
    interest_database,
):
    db = interest_database
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES (%s,'Fictional LEG')",
            (COMMUNITY,),
        )
    db.create_operator_api_client(
        COMMUNITY,
        "building-1",
        "Integrator",
        ["formation.read"],
        "token-hash-retry",
        "https://hooks.example.net/ingest",
    )
    db.create_operator_event(
        "formation.submission.prepared", "case-1", COMMUNITY, {"state": "prepared"}
    )

    def refusing(url, body, headers, timeout):
        return 503

    for _ in range(5):
        _make_due(db)
        operator_api.dispatch_pending_webhooks(transport=refusing, signing_key="k")
    _make_due(db)

    client = _admin_client()
    with patch(
        "operator_api.formation_wizard.get_community_status",
        return_value={
            "members": [
                {"building_id": "building-1", "status": "confirmed", "role": "admin"}
            ]
        },
    ):
        [failed] = client.get(DELIVERIES).get_json()["deliveries"]
        assert failed["status"] == "failed"

        rearmed = client.post(
            f"{DELIVERIES}/{failed['delivery_id']}/retry",
            headers={"X-CSRF-Token": "csrf-secret"},
        )
        assert rearmed.status_code == 200

        received = []

        def accepting(url, body, headers, timeout):
            received.append(headers["OpenLEG-Delivery"])
            return 204

        totals = operator_api.dispatch_pending_webhooks(
            transport=accepting, signing_key="k"
        )
        [after] = client.get(DELIVERIES).get_json()["deliveries"]

    assert received == [failed["delivery_id"]]
    assert totals == {"attempted": 1, "delivered": 1, "failed": 0}
    assert after["status"] == "delivered"
    # Re-arming grants one more attempt and keeps the attempt history.
    assert after["attempt_count"] == 6
