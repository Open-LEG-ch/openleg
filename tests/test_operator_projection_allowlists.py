# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exact public read inventories, including unexpected private store columns."""

from unittest.mock import patch

import pytest
from flask import Flask

import operator_api
from tests import test_interest_postgres

FORMATION = {
    "case_id",
    "community_id",
    "adapter_key",
    "contract_version",
    "state",
    "next_action",
    "response_status",
    "retryable",
    "failure_code",
    "created_at",
    "delivered_at",
    "acknowledged_at",
}
MEMBERSHIP = {
    "case_id",
    "mutation_id",
    "community_id",
    "mutation_type",
    "effective_date",
    "source_agreement_id",
    "state",
    "next_action",
    "response_status",
    "created_at",
    "updated_at",
}
EXTRA = {
    key: "private"
    for key in (
        "participant_id",
        "iban",
        "account_holder",
        "account_number",
        "debtor_name",
        "debtor_address",
        "manual_package",
        "before_facts",
        "after_facts",
        "response_evidence",
        "evidence",
        "created_by",
        "delivered_by",
        "capability_snapshot",
        "new_store_column",
        "webhook_secret",
        "token_hash",
        "policy_snapshot",
        "attachments",
        "message",
        "report",
    )
}


@pytest.fixture
def http_client():
    app = Flask(__name__)
    app.secret_key = "test-only"
    app.register_blueprint(operator_api.operator_api_bp)
    credential = {
        "id": "credential",
        "community_id": "community-a",
        "created_by": "admin",
        "rate_limit_per_hour": 100,
        "capabilities": list(operator_api.CAPABILITIES),
    }
    with (
        patch(
            "operator_api.db.get_operator_api_client_by_token_hash",
            return_value=credential,
        ),
        patch("operator_api.db.claim_operator_api_usage", return_value=True),
        patch(
            "operator_api.formation_wizard.get_community_status",
            return_value={
                "members": [
                    {"building_id": "admin", "role": "admin", "status": "confirmed"}
                ]
            },
        ),
    ):
        client = app.test_client()
        with client.session_transaction() as session:
            session["dashboard_building_id"] = "admin"
            session["dashboard_csrf_token"] = "csrf"
        yield client


def assert_read(client, path, store, envelope, fields):
    expected = {key: f"value-{key}" for key in fields}
    row = {**expected, **EXTRA}
    result = ([row], None) if envelope == "items" else [row]
    with patch(f"operator_api.db.{store}", return_value=result):
        response = client.get(
            path,
            headers={
                "Authorization": "Bearer token",
                "X-CSRF-Token": "csrf",
            },
        )
    assert response.status_code == 200
    item = response.get_json()[envelope][0]
    assert set(item) == fields
    assert item == expected


def test_formation_inventory(http_client):
    assert_read(
        http_client,
        "/api/operator/v1/communities/community-a/formation",
        "list_vnb_submission_cases",
        "submissions",
        FORMATION,
    )


def test_membership_inventory(http_client):
    assert_read(
        http_client,
        "/api/operator/v1/communities/community-a/membership-mutations",
        "list_vnb_mutations",
        "mutations",
        MEMBERSHIP,
    )


DELIVERIES = {
    "delivery_id",
    "status",
    "attempt_count",
    "response_status",
    "next_attempt_at",
    "last_attempt_at",
    "event_id",
    "event_type",
    "aggregate_id",
    "occurred_at",
}


def test_webhook_deliveries_inventory(http_client):
    assert_read(
        http_client,
        "/leg/community/community-a/operator-api/deliveries",
        "list_operator_webhook_deliveries",
        "deliveries",
        DELIVERIES,
    )


@pytest.mark.parametrize(
    ("path", "store", "fields"),
    [
        (
            "metering/jobs",
            "list_operator_metering_jobs",
            {
                "id",
                "territory",
                "started_at",
                "finished_at",
                "status",
                "attempts",
                "downloaded_files",
                "imported_files",
                "imported_readings",
                "error_code",
            },
        ),
        (
            "metering/calculated-deliveries",
            "list_operator_calculated_deliveries",
            {
                "id",
                "contract_version",
                "format_version",
                "transport",
                "community_id",
                "period_start",
                "period_end",
                "status",
                "diagnostics",
                "record_count",
                "received_at",
            },
        ),
        (
            "billing/periods",
            "list_operator_billing_periods",
            {
                "id",
                "community_id",
                "period_start",
                "period_end",
                "total_production_kwh",
                "total_allocated_kwh",
                "total_surplus_kwh",
                "total_network_discount_chf",
                "status",
            },
        ),
        (
            "billing/invoices",
            "list_operator_invoices",
            {
                "id",
                "invoice_number",
                "gross_chf",
                "issue_date",
                "due_date",
                "lifecycle_state",
            },
        ),
        (
            "billing/cases",
            "list_operator_invoice_cases",
            {
                "id",
                "invoice_id",
                "category",
                "status",
                "created_at",
                "updated_at",
            },
        ),
        (
            "payments/matches",
            "list_operator_payment_matches",
            {
                "id",
                "invoice_id",
                "entry_reference",
                "booking_date",
                "amount",
                "currency",
                "payment_reference",
                "is_reversal",
                "match_decision",
            },
        ),
    ],
)
def test_operational_inventory(http_client, path, store, fields):
    assert_read(
        http_client,
        f"/api/operator/v1/communities/community-a/{path}",
        store,
        "items",
        fields,
    )


def test_credentials_inventory(http_client):
    fields = {
        "id",
        "community_id",
        "name",
        "capabilities",
        "rate_limit_per_hour",
        "active",
        "created_at",
        "updated_at",
        "last_used_at",
        "webhook_url",
    }
    expected = {key: f"value-{key}" for key in fields}
    with patch(
        "operator_api.db.list_operator_api_clients",
        return_value=[{**expected, **EXTRA}],
    ):
        response = http_client.get(
            "/leg/community/community-a/operator-api/credentials",
            headers={"X-CSRF-Token": "csrf"},
        )
    assert response.status_code == 200
    item = response.get_json()["credentials"][0]
    assert set(item) == fields | {"webhook_requires_rotation"}
    assert item == {**expected, "webhook_requires_rotation": False}


interest_database = test_interest_postgres.interest_database


@pytest.mark.integration
def test_real_membership_read_excludes_participant(interest_database, http_client):
    with interest_database.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO communities(community_id,name) VALUES (%s,'Fictional LEG')",
            ("community-a",),
        )
        cur.execute(
            """INSERT INTO vnb_mutation_cases
            (case_id,mutation_id,community_id,participant_id,created_by,
             mutation_type,effective_date,source_agreement_id,before_facts,after_facts,
             adapter_key,contract_version,payload_fingerprint,state,next_action,manual_package)
            VALUES ('case-a','mutation-a','community-a','private-participant','admin',
                    'change','2026-10-01','agreement-v3','{}','{}',
                    'manual-handover','vnb-membership/1',%s,'acknowledged','none',%s)""",
            ("b" * 64, b"private package"),
        )
        conn.commit()
    response = http_client.get(
        "/api/operator/v1/communities/community-a/membership-mutations",
        headers={"Authorization": "Bearer token"},
    )
    assert response.status_code == 200
    item = response.get_json()["mutations"][0]
    assert set(item) == MEMBERSHIP
    assert item["mutation_id"] == "mutation-a"
    assert item["case_id"] == "case-a"
    assert "private-participant" not in response.get_data(as_text=True)
