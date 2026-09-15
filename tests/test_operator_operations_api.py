# SPDX-License-Identifier: AGPL-3.0-or-later
"""Authenticated HTTP and signed-event seams for issue #611."""

from unittest.mock import patch

import operator_api

CLIENT = {
    "id": "client-1",
    "community_id": "community-a",
    "created_by": "operator-a",
    "capabilities": [
        "metering.read",
        "metering.mutate",
        "billing.read",
        "cases.read",
        "cases.mutate",
        "payments.read",
        "payments.mutate",
    ],
    "rate_limit_per_hour": 100,
}


def _client(app):
    if "operator_api" not in app.blueprints:
        app.register_blueprint(operator_api.operator_api_bp)
    return app.test_client()


def _headers(key=None):
    headers = {"Authorization": "Bearer token"}
    if key:
        headers["Idempotency-Key"] = key
    return headers


@patch("operator_api.db.claim_operator_api_usage", return_value=True)
@patch("operator_api.db.get_operator_api_client_by_token_hash", return_value=CLIENT)
@patch("operator_api.db.list_operator_metering_jobs")
def test_metering_reads_are_scoped_filtered_paginated_and_redacted(
    rows, _lookup, _usage, app
):
    rows.return_value = (
        [
            {
                "id": 7,
                "status": "failure",
                "error_code": "fetch_failed",
                "report": {"secret": "x"},
            }
        ],
        8,
    )
    response = _client(app).get(
        "/api/operator/v1/communities/community-a/metering/jobs?status=failure&limit=1&cursor=7",
        headers=_headers(),
    )
    assert response.status_code == 200
    assert response.get_json() == {
        "schema_version": "operator-api/1",
        "items": [{"id": 7, "status": "failure", "error_code": "fetch_failed"}],
        "next_cursor": "8",
    }
    rows.assert_called_once_with("community-a", status="failure", limit=1, cursor="7")


@patch("operator_api.db.claim_operator_api_usage", return_value=True)
@patch("operator_api.db.get_operator_api_client_by_token_hash", return_value=CLIENT)
@patch("operator_api.db.list_operator_invoices")
def test_invoice_projection_never_discloses_or_accepts_snapshot_fields(
    rows, _lookup, _usage, app
):
    rows.return_value = (
        [
            {
                "id": 3,
                "invoice_number": "LEG-3",
                "gross_chf": "42.00",
                "lifecycle_state": "issued",
                "policy_snapshot": {"private": True},
            }
        ],
        None,
    )
    response = _client(app).get(
        "/api/operator/v1/communities/community-a/billing/invoices", headers=_headers()
    )
    assert response.get_json()["items"] == [
        {
            "id": 3,
            "invoice_number": "LEG-3",
            "gross_chf": "42.00",
            "lifecycle_state": "issued",
        }
    ]
    assert (
        _client(app)
        .patch(
            "/api/operator/v1/communities/community-a/billing/invoices/3",
            headers=_headers(),
            json={"gross_chf": "0"},
        )
        .status_code
        == 404
    )


@patch("operator_api.sdat_ingestion.run")
@patch("operator_api.db.get_operator_ingestion_retry")
@patch("operator_api.db.claim_operator_api_usage", return_value=True)
@patch("operator_api.db.get_operator_api_client_by_token_hash", return_value=CLIENT)
def test_retry_runs_only_the_tenant_scoped_eligible_schedule(
    _lookup, _usage, eligible, run, app
):
    eligible.return_value = {
        "territory": "tenant-a",
        "enabled": True,
        "max_attempts": 2,
    }
    run.return_value = {"status": "success", "territory": "tenant-a"}
    response = _client(app).post(
        "/api/operator/v1/communities/community-a/metering/jobs/7/retry",
        headers=_headers("retry-7"),
    )
    assert response.status_code == 200
    eligible.assert_called_once_with("community-a", 7)
    run.assert_called_once_with("tenant-a", eligible.return_value)


@patch("operator_api.db.claim_operator_api_usage", return_value=True)
@patch("operator_api.db.get_operator_api_client_by_token_hash", return_value=CLIENT)
@patch("operator_api.db.respond_operator_invoice_case")
def test_case_response_requires_idempotency_and_uses_atomic_domain_action(
    action, _lookup, _usage, app
):
    action.return_value = {
        "id": 9,
        "status": "acknowledged",
        "replayed": False,
        "event_id": "evt-9",
    }
    client = _client(app)
    missing = client.post(
        "/api/operator/v1/communities/community-a/billing/cases/9/responses",
        headers=_headers(),
        json={"message": "Geprüft", "status": "acknowledged"},
    )
    response = client.post(
        "/api/operator/v1/communities/community-a/billing/cases/9/responses",
        headers=_headers("case-9-v1"),
        json={"message": "Geprüft", "status": "acknowledged"},
    )
    assert missing.status_code == 400
    assert response.status_code == 200
    action.assert_called_once_with(
        9, "community-a", "operator-a", "Geprüft", "acknowledged", "case-9-v1"
    )


@patch("operator_api.db.claim_operator_api_usage", return_value=True)
@patch("operator_api.db.get_operator_api_client_by_token_hash", return_value=CLIENT)
@patch("operator_api.db.confirm_operator_payment_match")
def test_payment_confirmation_has_tenant_and_replay_at_domain_seam(
    action, _lookup, _usage, app
):
    action.return_value = {
        "id": 12,
        "match_decision": "matched",
        "replayed": True,
        "event_id": "evt-12",
    }
    response = _client(app).post(
        "/api/operator/v1/communities/community-a/payments/matches/12/confirm",
        headers=_headers("payment-12"),
        json={"invoice_id": 3},
    )
    assert response.status_code == 200
    action.assert_called_once_with(12, 3, "community-a", "operator-a", "payment-12")
    assert response.get_json()["replayed"] is True


@patch("operator_api.db.claim_operator_api_usage", return_value=True)
@patch("operator_api.db.get_operator_api_client_by_token_hash")
def test_cross_tenant_and_insufficient_capability_fail_closed(lookup, _usage, app):
    lookup.side_effect = [CLIENT, {**CLIENT, "capabilities": ["billing.read"]}]
    client = _client(app)
    assert (
        client.get(
            "/api/operator/v1/communities/community-b/billing/invoices",
            headers=_headers(),
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/api/operator/v1/communities/community-a/metering/jobs", headers=_headers()
        ).status_code
        == 403
    )


def test_minimal_signed_event_payload_excludes_member_and_bank_details():
    event = operator_api.operational_event(
        "payment.match.confirmed",
        "12",
        "community-a",
        {
            "status": "matched",
            "participant_id": "p-1",
            "iban": "CH00",
            "message": "private",
        },
    )
    assert event["payload"] == {"status": "matched"}


def test_event_subscription_capability_matches_the_minimal_payload_domain():
    from store.operator_api import _subscription_capability

    assert _subscription_capability("metering.ingestion.completed") == "metering.read"
    assert _subscription_capability("invoice.case.updated") == "cases.read"
    assert _subscription_capability("payment.match.confirmed") == "payments.read"
