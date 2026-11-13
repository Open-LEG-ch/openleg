# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every contracted preview rejects malformed options through the same parser."""

from unittest.mock import Mock

import pytest

import operator_request
import vnb_exchange
from tests import operator_contract_helpers as helpers
from tests.test_operator_contract import METHODS, load_contract

interest_database = helpers.interest_database
operator_http = helpers.operator_http
pytestmark = pytest.mark.integration


def dry_run_operations():
    spec = load_contract()
    operations = []
    for path, item in spec["paths"].items():
        for method in METHODS & item.keys():
            operation = item[method]
            parameters = item.get("parameters", []) + operation.get("parameters", [])
            if any(
                helpers.resolve(spec, parameter).get("name") == "dry_run"
                and helpers.resolve(spec, parameter).get("in") == "query"
                for parameter in parameters
            ):
                operations.append(
                    pytest.param(
                        path,
                        method,
                        operation["operationId"],
                        id=operation["operationId"],
                    )
                )
    assert operations, "Contract has no dry_run operations"
    return operations


@pytest.mark.parametrize("path,method,operation_id", dry_run_operations())
@pytest.mark.parametrize(
    "query,body",
    [
        ("dry_run=true&dry_run=true", {}),
        ("dry_run=false&dry_run=false", {}),
        ("dry_run=TRUE", {}),
        ("dry_run=1", {}),
        ("dry_run=", {}),
        ("", {"dry_run": True}),
        ("", {"dry_run": False}),
        ("dry_run=true", {"dry_run": False}),
        ("dry_run=true&dry_run=false", {}),
        ("dry_run=false&dry_run=true", {}),
    ],
)
def test_malformed_dry_run_uses_shared_parser_before_domain(
    operator_http, monkeypatch, path, method, operation_id, query, body
):
    http = operator_http
    ids = helpers.seed_operator_resources(http)
    parser = Mock(wraps=operator_request.parse_dry_run)
    monkeypatch.setattr(operator_request, "parse_dry_run", parser)

    def forbidden(*args, **kwargs):
        pytest.fail("Malformed dry_run reached a domain call")

    monkeypatch.setattr(vnb_exchange, "submit_formation", forbidden)
    monkeypatch.setattr(vnb_exchange, "submit_membership_mutation", forbidden)
    monkeypatch.setattr(http.db, "respond_operator_invoice_case", forbidden)
    monkeypatch.setattr(http.db, "confirm_operator_payment_match", forbidden)
    response = http.client.open(
        path.format(community_id=http.community_id, **ids),
        method=method.upper(),
        query_string=query,
        json={**body},
        headers={**http.headers, "Idempotency-Key": "malformed-preview"},
    )
    assert response.status_code == 400, response.json
    expected = {
        "schema_version": "operator-api/1",
        "code": "invalid_request",
        "retryable": False,
        "error": "Invalid dry_run option",
    }
    if operation_id == "respondInvoiceCase":
        expected.update(error="Invalid dry_run", field="dry_run")
    assert response.json == expected
    # A private copy can reject every malformed input and still drift later.
    # Require the HTTP request to have actually invoked the shared policy.
    parser.assert_called_once()
    assert parser.call_args.args[0].getlist("dry_run") == [
        part.split("=", 1)[1] for part in query.split("&") if part
    ]
    assert parser.call_args.args[1] == body
