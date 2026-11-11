# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from flask import Flask

import operator_api
from tests.operator_contract_helpers import validate_response
from tests.test_operator_contract import load_contract

PREFIX = "/api/operator/v1/communities/"


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(operator_api.operator_api_bp)
    monkeypatch.setattr(
        operator_api.db,
        "get_operator_api_client_by_token_hash",
        lambda _: {
            "id": "c",
            "community_id": "leg",
            "capabilities": list(operator_api.CAPABILITIES),
            "rate_limit_per_hour": 100,
            "created_by": "admin",
        },
    )
    monkeypatch.setattr(operator_api.db, "claim_operator_api_usage", lambda *a: True)
    return app.test_client()


@pytest.mark.parametrize(
    "suffix,headers,status,code",
    [
        ("billing/periods", {}, 401, "authentication_required"),
        (
            "billing/periods?limit=0",
            {"Authorization": "Bearer x"},
            400,
            "invalid_pagination",
        ),
    ],
)
def test_boundary_codes(client, suffix, headers, status, code):
    response = client.get(PREFIX + "leg/" + suffix, headers=headers)
    assert response.status_code == status
    assert response.json["code"] == code
    assert response.json["retryable"] is False
    validate_response(
        load_contract(), PREFIX + "{community_id}/billing/periods", "get", response
    )


@pytest.mark.parametrize(
    "kind,code",
    [
        ("invalid", "invalid_formation"),
        ("conflict", "conflict"),
        ("unavailable", "unavailable"),
    ],
)
def test_domain_reasons_are_safe(client, monkeypatch, kind, code):
    errors = {
        "invalid": operator_api.vnb_exchange.FormationSubmissionInvalid,
        "conflict": operator_api.db.VnbSubmissionConflict,
        "unavailable": operator_api.db.VnbExchangeStoreError,
    }

    def fail(*args):
        raise errors[kind]("private participant stack detail")

    monkeypatch.setattr(operator_api.vnb_exchange, "submit_formation", fail)
    response = client.post(
        PREFIX + "leg/formation/submissions", headers={"Authorization": "Bearer x"}
    )
    assert response.status_code == (503 if kind == "unavailable" else 409)
    assert response.json["code"] == code
    assert "private participant" not in response.text


def test_contract_rejects_undeclared_code(client):
    from jsonschema import ValidationError

    response = client.get(PREFIX + "leg/billing/periods")
    body = response.json
    body["code"] = "undeclared"
    response.set_data(__import__("json").dumps(body))
    with pytest.raises(ValidationError):
        validate_response(
            load_contract(), PREFIX + "{community_id}/billing/periods", "get", response
        )


def test_application_ip_limit_uses_operator_contract(client):
    from flask import abort

    from app import handle_rate_limit

    app = client.application
    app.register_error_handler(429, handle_rate_limit)

    @app.before_request
    def limited():
        abort(429)

    response = client.get(PREFIX + "leg/billing/periods")
    assert response.status_code == 429
    assert response.json["code"] == "rate_limited"
    assert response.json["retryable"] is True
    assert int(response.headers["Retry-After"]) > 0
    validate_response(
        load_contract(), PREFIX + "{community_id}/billing/periods", "get", response
    )


@pytest.mark.parametrize(
    "method,suffix,payload,key,seam,value,status,code",
    [
        (
            "get",
            "formation",
            None,
            None,
            "get_operator_api_client_by_token_hash",
            None,
            401,
            "invalid_credential",
        ),
        (
            "get",
            "formation",
            None,
            None,
            "get_operator_api_client_by_token_hash",
            {"community_id": "foreign"},
            404,
            "not_found",
        ),
        (
            "get",
            "formation",
            None,
            None,
            "get_operator_api_client_by_token_hash",
            {"community_id": "leg", "capabilities": []},
            403,
            "capability_denied",
        ),
        (
            "get",
            "formation",
            None,
            None,
            "claim_operator_api_usage",
            False,
            429,
            "rate_limited",
        ),
        ("post", "membership-mutations", [], None, None, None, 400, "invalid_request"),
        (
            "post",
            "formation/submissions",
            {},
            None,
            "submit_formation",
            "forbidden",
            403,
            "capability_denied",
        ),
        (
            "post",
            "membership-mutations",
            {},
            None,
            "submit_membership_mutation",
            "forbidden",
            403,
            "capability_denied",
        ),
        (
            "post",
            "membership-mutations",
            {},
            None,
            "submit_membership_mutation",
            "membership",
            409,
            "invalid_membership",
        ),
        (
            "post",
            "membership-mutations",
            {},
            None,
            "submit_membership_mutation",
            "conflict",
            409,
            "conflict",
        ),
        (
            "post",
            "membership-mutations",
            {},
            None,
            "submit_membership_mutation",
            "unavailable",
            503,
            "unavailable",
        ),
        (
            "post",
            "metering/jobs/1/retry",
            {},
            None,
            None,
            None,
            400,
            "invalid_idempotency_key",
        ),
        (
            "post",
            "metering/jobs/1/retry",
            {},
            "key",
            "claim_operator_ingestion_retry",
            None,
            404,
            "not_found",
        ),
        (
            "post",
            "metering/jobs/1/retry",
            {},
            "key",
            "claim_operator_ingestion_retry",
            {"pending": True},
            409,
            "retry_in_progress",
        ),
        (
            "post",
            "billing/cases/1/responses",
            {},
            None,
            None,
            None,
            400,
            "invalid_idempotency_key",
        ),
        (
            "post",
            "billing/cases/1/responses",
            [],
            "key",
            None,
            None,
            400,
            "invalid_request",
        ),
        (
            "post",
            "billing/cases/1/responses",
            {},
            "key",
            "respond_operator_invoice_case",
            None,
            404,
            "not_found",
        ),
        (
            "post",
            "payments/matches/1/confirm",
            {},
            None,
            None,
            None,
            400,
            "invalid_idempotency_key",
        ),
        (
            "post",
            "payments/matches/1/confirm",
            [],
            "key",
            None,
            None,
            400,
            "invalid_request",
        ),
        (
            "post",
            "payments/matches/1/confirm",
            {},
            "key",
            None,
            None,
            400,
            "invalid_payment",
        ),
        (
            "post",
            "payments/matches/1/confirm",
            {"invoice_id": 1},
            "key",
            "confirm_operator_payment_match",
            None,
            404,
            "not_found",
        ),
    ],
)
def test_expected_route_failures(
    client, monkeypatch, method, suffix, payload, key, seam, value, status, code
):
    if seam:
        target = (
            operator_api.vnb_exchange if seam.startswith("submit_") else operator_api.db
        )

        def outcome(*args):
            if seam.startswith("submit_"):
                errors = {
                    "forbidden": operator_api.vnb_exchange.FormationSubmissionForbidden,
                    "membership": operator_api.vnb_exchange.ParticipantMutationInvalid,
                    "conflict": operator_api.db.VnbSubmissionConflict,
                    "unavailable": operator_api.db.VnbExchangeStoreError,
                }
                raise errors[value]("private participant stack detail")
            return value

        monkeypatch.setattr(target, seam, outcome)
    headers = {"Authorization": "Bearer x"}
    if key:
        headers["Idempotency-Key"] = key
    response = client.open(
        PREFIX + "leg/" + suffix, method=method, headers=headers, json=payload
    )
    assert response.status_code == status
    assert response.json["code"] == code
    assert response.json["retryable"] is (status in (429, 503))
    assert "private participant" not in response.text
    if status == 429:
        assert response.headers["Retry-After"] == "3600"
