# SPDX-License-Identifier: AGPL-3.0-or-later
"""Authenticated HTTP and signed-webhook contracts for operator integrations."""

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
