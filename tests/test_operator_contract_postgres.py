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
    for suffix, body, status in writes:
        http.headers, _ = http.credential_headers(
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
    for path, operations in spec["paths"].items():
        if "get" not in operations:
            continue
        http.headers, _ = http.credential_headers(
            [operations["get"]["x-openleg-capability"]]
        )
        response = http.request("GET", path.removeprefix(prefix))
        assert response.status_code == 200
        validate_response(spec, path, "get", response)
        body = response.get_json()
        items = body.get("items", body.get("submissions", body.get("mutations")))
        assert items, path
        covered.add(("get", path))
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
    for method, suffix, body, query, headers, status in scenarios:
        response = http.request(
            method.upper(),
            suffix.format(**ids),
            json=body,
            query_string=query,
            headers=headers,
        )
        assert response.status_code == status, response.get_json()
        validate_response(spec, prefix + suffix, method, response)
