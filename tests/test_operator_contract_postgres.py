# SPDX-License-Identifier: AGPL-3.0-or-later
"""Validate actual PostgreSQL-backed operator HTTP responses against OpenAPI."""

import pytest

from tests import operator_contract_helpers
from tests.operator_contract_helpers import (
    seed_operator_resources,
    validate_response,
)
from tests.test_operator_contract import load_contract

interest_database = operator_contract_helpers.interest_database
operator_http = operator_contract_helpers.operator_http
pytestmark = pytest.mark.integration


def test_every_operation_with_real_store_and_bearer(operator_http, monkeypatch):
    http = operator_http
    ids = seed_operator_resources(http)
    spec = load_contract()
    prefix = "/api/operator/v1/communities/{community_id}"
    # Default Datahub fetch fails locally without credentials, without networking.
    monkeypatch.delenv("SWISSELDEX_FTPS_USER", raising=False)
    monkeypatch.delenv("SWISSELDEX_FTPS_PASSWORD", raising=False)
    writes = [
        ("/formation/submissions", {}, 202),
        (
            "/membership-mutations",
            {
                "mutation_id": "contract-change",
                "participant_id": "contract-admin",
                "mutation_type": "change",
                "effective_date": "2026-12-01",
                "source_agreement_id": "contract-agreement",
                "after": {"access_roles": ["auditor"]},
            },
            202,
        ),
        ("/metering/jobs/{job_id}/retry", {}, 200),
        (
            "/billing/cases/{case_id}/responses",
            {"message": "Checked the calculation.", "status": "acknowledged"},
            200,
        ),
        (
            "/payments/matches/{entry_id}/confirm",
            {"invoice_id": ids["invoice_id"]},
            200,
        ),
    ]
    covered = set()
    # One credential per capability, revoked after use: a community may hold
    # at most ten active credentials.
    for suffix, body, status in writes:
        http.headers, credential = http.credential_headers(
            [spec["paths"][prefix + suffix]["post"]["x-openleg-capability"]]
        )
        response = http.request(
            "POST",
            suffix.format(**ids),
            json=body,
            headers={"Idempotency-Key": "contract-key"},
        )
        assert response.status_code == status, response.get_json()
        validate_response(spec, prefix + suffix, "post", response)
        covered.add(("post", prefix + suffix))
        replay = http.request(
            "POST",
            suffix.format(**ids),
            json=body,
            headers={"Idempotency-Key": "contract-key"},
        )
        assert replay.status_code == status
        validate_response(spec, prefix + suffix, "post", replay)
        http.db.revoke_operator_api_client(http.community_id, credential["id"])
    for path, operations in spec["paths"].items():
        if "get" not in operations:
            continue
        http.headers, credential = http.credential_headers(
            [operations["get"]["x-openleg-capability"]]
        )
        response = http.request("GET", path.removeprefix(prefix))
        assert response.status_code == 200
        validate_response(spec, path, "get", response)
        body = response.get_json()
        items = body.get("items", body.get("submissions", body.get("mutations")))
        assert items, path
        covered.add(("get", path))
        http.db.revoke_operator_api_client(http.community_id, credential["id"])
    assert covered == {
        (method, path)
        for path, operations in spec["paths"].items()
        for method in operations
    }


def test_authentication_capability_scope_and_rate_errors(operator_http):
    http = operator_http
    spec = load_contract()
    denied, _ = http.credential_headers([])
    limited, _ = http.credential_headers(
        [
            "formation.read",
            "formation.mutate",
            "membership.read",
            "membership.mutate",
            "metering.read",
            "metering.mutate",
            "billing.read",
            "cases.read",
            "cases.mutate",
            "payments.read",
            "payments.mutate",
        ],
        budget=1,
    )
    assert (
        http.client.get(
            "/api/operator/v1/communities/contract-leg/formation", headers=limited
        ).status_code
        == 200
    )
    for path, operations in spec["paths"].items():
        concrete = path.format(
            community_id=http.community_id, job_id=1, case_id=1, entry_id=1
        )
        for method in operations:
            for headers, url, status in [
                ({}, concrete, 401),
                (
                    {
                        "Authorization": http.headers["Authorization"].removeprefix(
                            "Bearer "
                        )
                    },
                    concrete,
                    401,
                ),
                (denied, concrete, 403),
                (http.headers, concrete.replace("contract-leg", "foreign-leg"), 404),
                (limited, concrete, 429),
            ]:
                response = http.client.open(url, method=method.upper(), headers=headers)
                assert response.status_code == status
                assert (
                    response.json["code"]
                    == {
                        401: "authentication_required",
                        403: "capability_denied",
                        404: "not_found",
                        429: "rate_limited",
                    }[status]
                )
                validate_response(spec, path, method, response)


def test_validation_conflict_and_missing_resource_errors(operator_http):
    http = operator_http
    ids = seed_operator_resources(http)
    spec = load_contract()
    prefix = "/api/operator/v1/communities/{community_id}"
    scenarios = [
        ("get", "/billing/periods", {}, {"limit": 0}, {}, 400),
        ("post", "/membership-mutations", [], {}, {}, 400),
        ("post", "/membership-mutations", {}, {}, {}, 409),
        ("post", "/metering/jobs/{job_id}/retry", {}, {}, {}, 400),
        ("post", "/billing/cases/{case_id}/responses", {}, {}, {}, 400),
        ("post", "/payments/matches/{entry_id}/confirm", {}, {}, {}, 400),
        (
            "post",
            "/payments/matches/{entry_id}/confirm",
            {},
            {},
            {"Idempotency-Key": "invalid"},
            400,
        ),
        (
            "post",
            "/billing/cases/{case_id}/responses",
            {"message": "hello", "status": "invalid"},
            {},
            {"Idempotency-Key": "invalid"},
            409,
        ),
        (
            "post",
            "/payments/matches/{entry_id}/confirm",
            {"invoice_id": 999999},
            {},
            {"Idempotency-Key": "missing"},
            409,
        ),
    ]
    expected_codes = [
        "invalid_pagination",
        "invalid_request",
        "invalid_membership",
        "invalid_idempotency_key",
        "invalid_idempotency_key",
        "invalid_idempotency_key",
        "invalid_payment",
        "invalid_case",
        "invalid_payment",
    ]
    for (method, suffix, body, query, headers, status), code in zip(
        scenarios, expected_codes, strict=True
    ):
        response = http.request(
            method.upper(),
            suffix.format(**ids),
            json=body,
            query_string=query,
            headers=headers,
        )
        assert response.status_code == status, response.get_json()
        assert response.json["code"] == code
        validate_response(spec, prefix + suffix, method, response)


def test_sql_idempotency_conflict_has_distinct_reason(operator_http):
    http = operator_http
    ids = seed_operator_resources(http)
    suffix = "/billing/cases/{case_id}/responses".format(**ids)
    headers = {"Idempotency-Key": "same-key"}
    first = http.request(
        "POST",
        suffix,
        headers=headers,
        json={"message": "one", "status": "acknowledged"},
    )
    assert first.status_code == 200
    conflict = http.request(
        "POST", suffix, headers=headers, json={"message": "two", "status": "resolved"}
    )
    assert conflict.status_code == 409
    assert conflict.json["code"] == "idempotency_conflict"
    invalid = http.request(
        "POST",
        suffix,
        headers={"Idempotency-Key": "new-key"},
        json={"message": "two", "status": "open"},
    )
    assert invalid.status_code == 409
    assert invalid.json["code"] == "invalid_case"


def test_missing_resources_and_pending_retry_from_sql(operator_http):
    http = operator_http
    ids = seed_operator_resources(http)
    for suffix, payload in [
        ("/metering/jobs/999999/retry", {}),
        ("/billing/cases/999999/responses", {"message": "hello", "status": "resolved"}),
        ("/payments/matches/999999/confirm", {"invoice_id": ids["invoice_id"]}),
    ]:
        response = http.request(
            "POST", suffix, json=payload, headers={"Idempotency-Key": "missing"}
        )
        assert response.status_code == 404
        assert response.json["code"] == "not_found"
    http.db.claim_operator_ingestion_retry(http.community_id, ids["job_id"], "pending")
    response = http.request(
        "POST",
        "/metering/jobs/{job_id}/retry".format(**ids),
        headers={"Idempotency-Key": "pending"},
    )
    assert response.status_code == 409
    assert response.json["code"] == "retry_in_progress"


def test_formation_not_ready_from_sql(operator_http):
    response = operator_http.request("POST", "/formation/submissions", json={})
    assert response.status_code == 409
    assert response.json["code"] == "invalid_formation"
