# SPDX-License-Identifier: AGPL-3.0-or-later
"""Current community authority at the operator HTTP boundary."""

from unittest.mock import patch

import pytest

import operator_api
from cron import cron_bp

CREATOR = {"building_id": "admin-a", "role": "admin", "status": "confirmed"}
CLIENT = {
    "id": "client-1",
    "community_id": "community-a",
    "created_by": "admin-a",
    "capabilities": ["formation.read", "payments.mutate"],
    "rate_limit_per_hour": 100,
}


def http_client(app, actor="admin-a"):
    app.config["SECRET_KEY"] = "authority-test"
    if "operator_api" not in app.blueprints:
        app.register_blueprint(operator_api.operator_api_bp)
    client = app.test_client()
    with client.session_transaction() as session:
        session["dashboard_building_id"] = actor
        session["dashboard_csrf_token"] = "csrf"
    return client


@pytest.mark.parametrize(
    "members",
    [
        [{**CREATOR, "role": "member", "access_roles": ["billing_approver"]}],
        [{**CREATOR, "status": "exited"}],
        [{**CREATOR, "status": "pending"}],
        [],
        [{**CREATOR, "building_id": "other-admin"}],
    ],
)
@pytest.mark.parametrize(
    "method,path,body,store",
    [
        ("get", "formation", None, "list_vnb_submission_cases"),
        (
            "post",
            "payments/matches/12/confirm",
            {"invoice_id": 3},
            "confirm_operator_payment_match",
        ),
    ],
)
def test_community_authority_loss_denies_operator_reads_and_payments(
    app, members, method, path, body, store
):
    client = http_client(app)
    with (
        patch(
            "operator_api.db.get_operator_api_client_by_token_hash", return_value=CLIENT
        ),
        patch("operator_api.db.claim_operator_api_usage", return_value=True),
        patch(
            "operator_api.formation_wizard.get_community_status",
            return_value={"members": members},
        ) as status,
        patch(
            "operator_api.db." + store,
            return_value=[] if method == "get" else {"id": 12},
        ) as action,
    ):
        response = getattr(client, method)(
            "/api/operator/v1/communities/community-a/" + path,
            headers={"Authorization": "Bearer token", "Idempotency-Key": "payment-12"},
            json=body,
        )
    assert response.status_code == 403
    assert response.json == {"error": "Forbidden", "schema_version": "operator-api/1"}
    action.assert_not_called()
    status.assert_called_once_with("community-a", "admin-a")


def test_other_community_administrator_cannot_rotate_creator_credential(app):
    client = http_client(app, "other-admin")
    with (
        patch(
            "operator_api.formation_wizard.get_community_status",
            return_value={"members": [{**CREATOR, "building_id": "other-admin"}]},
        ),
        patch("operator_api.db.list_operator_api_clients", return_value=[CLIENT]),
        patch(
            "operator_api.db.rotate_operator_api_client",
            return_value={**CLIENT, "webhook_secret": "secret"},
        ) as rotate,
    ):
        response = client.post(
            "/leg/community/community-a/operator-api/credentials/client-1/rotate",
            headers={"X-CSRF-Token": "csrf"},
        )
    assert response.status_code == 403
    assert response.json == {"error": "Forbidden", "schema_version": "operator-api/1"}
    rotate.assert_not_called()


@pytest.mark.parametrize(
    "member", [{**CREATOR, "role": "member"}, {**CREATOR, "status": "exited"}]
)
def test_cron_fails_webhooks_after_community_authority_loss(app, member):
    from tests.test_operator_api import _delivery_row

    if "cron" not in app.blueprints:
        app.register_blueprint(cron_bp)
    app.config["CRON_SECRET"] = "cron-test"
    rows = [
        _delivery_row(created_by="admin-a", delivery_id=f"delivery-{i}")
        for i in range(2)
    ]
    with (
        patch("operator_api.db.get_pending_webhook_deliveries", return_value=rows),
        patch("operator_api.db.record_webhook_attempt") as record,
        patch(
            "operator_api.formation_wizard.get_community_status",
            return_value={"members": [member]},
        ) as status,
        patch("operator_api._default_transport", return_value=204) as transport,
    ):
        response = app.test_client().post(
            "/api/cron/process-operator-webhooks",
            headers={"X-Cron-Secret": "cron-test"},
        )
    assert response.status_code == 200
    assert response.json == {"attempted": 2, "delivered": 0, "failed": 2}
    transport.assert_not_called()
    status.assert_called_once_with("community-a", "admin-a")
    assert record.call_count == 2
    for index, attempt in enumerate(record.call_args_list):
        assert attempt.args == (f"delivery-{index}",)
        assert attempt.kwargs == {
            "claim_id": "claim-1",
            "status": "failed",
            "response_status": 0,
            "retryable": False,
        }


def test_cron_retries_webhooks_when_community_authority_cannot_be_loaded(app):
    from tests.test_operator_api import _delivery_row

    if "cron" not in app.blueprints:
        app.register_blueprint(cron_bp)
    app.config["CRON_SECRET"] = "cron-test"
    with (
        patch(
            "operator_api.db.get_pending_webhook_deliveries",
            return_value=[_delivery_row(created_by="admin-a")],
        ),
        patch("operator_api.db.record_webhook_attempt") as record,
        patch("operator_api.formation_wizard.get_community_status", return_value=None),
        patch("operator_api._default_transport", return_value=204) as transport,
    ):
        response = app.test_client().post(
            "/api/cron/process-operator-webhooks",
            headers={"X-Cron-Secret": "cron-test"},
        )
    assert response.json == {"attempted": 1, "delivered": 0, "failed": 1}
    transport.assert_not_called()
    assert record.call_args.kwargs == {
        "claim_id": "claim-1",
        "status": "retry",
        "response_status": 0,
        "retryable": True,
    }
