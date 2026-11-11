# SPDX-License-Identifier: AGPL-3.0-or-later
"""The committed contract must cover the live operator HTTP surface exactly."""

import re
from pathlib import Path

import pytest
import yaml
from flask import Flask

from operator_api import operator_api_bp

SPEC_PATH = Path(__file__).resolve().parents[1] / "docs/openapi/operator-v1.yaml"
METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def load_contract():
    return yaml.safe_load(SPEC_PATH.read_text())


def make_operator_app():
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic-contract-session")
    app.register_blueprint(operator_api_bp)
    return app


@pytest.fixture
def full_operator_app():
    from app import create_app

    return create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "synthetic-contract-session",
            "APP_BASE_URL": "http://localhost",
            "RATELIMIT_STORAGE_URI": "memory://",
            "RATELIMIT_ENABLED": True,
        },
        load_environment=False,
        check_database=False,
    )


def test_application_rate_limit_response_matches_contract(full_operator_app):
    from tests.operator_contract_helpers import validate_response

    path = "/api/operator/v1/communities/{community_id}/formation"
    client = full_operator_app.test_client()
    # Missing credentials avoid store access while exercising the real IP limit.
    for _ in range(500):
        assert client.get(path.format(community_id="x")).status_code == 401
    response = client.get(path.format(community_id="x"))
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert response.get_json()["retry_after_seconds"] == 60
    assert "schema_version" not in response.get_json()
    validate_response(load_contract(), path, "get", response)


def assert_route_coverage(app, spec):
    actual = {
        (method.lower(), re.sub(r"<(?:[^:<>]+:)?([^<>]+)>", r"{\1}", rule.rule))
        for rule in app.url_map.iter_rules()
        if rule.rule.startswith("/api/operator/v1")
        for method in rule.methods - {"HEAD", "OPTIONS"}
    }
    documented = {
        (method, path)
        for path, item in spec["paths"].items()
        for method in item
        if method in METHODS
    }
    assert actual == documented, (
        f"Undocumented: {actual - documented}; stale: {documented - actual}"
    )


def test_operator_route_coverage(full_operator_app):
    assert_route_coverage(full_operator_app, load_contract())


@pytest.mark.parametrize("mutation", ["remove", "add_route", "stale_spec"])
def test_route_coverage_rejects_drift(mutation, full_operator_app):
    spec = load_contract()
    app = full_operator_app
    if mutation == "remove":
        del spec["paths"][next(iter(spec["paths"]))]["get"]
    elif mutation == "add_route":
        app.add_url_rule(
            "/api/operator/v1/undocumented", "operator_api.undocumented", dict
        )
    else:
        spec["paths"]["/api/operator/v1/stale"] = {"post": {}}
    with pytest.raises(AssertionError, match="Undocumented:"):
        assert_route_coverage(app, spec)


def test_route_coverage_rejects_route_outside_operator_blueprint(full_operator_app):
    full_operator_app.add_url_rule(
        "/api/operator/v1/undocumented", "undocumented", dict
    )
    with pytest.raises(AssertionError, match="undocumented"):
        assert_route_coverage(full_operator_app, load_contract())


def test_operation_metadata_and_schema_references():
    from jsonschema import Draft202012Validator

    from operator_api import CAPABILITIES
    from tests.operator_contract_helpers import resolve

    spec = load_contract()
    assert spec["openapi"] == "3.1.0"
    identities = []
    for item in spec["paths"].values():
        for operation in item.values():
            identities.append(operation["operationId"])
            assert operation["x-openleg-capability"] in CAPABILITIES
            assert operation["responses"]
            for parameter in operation["parameters"]:
                assert resolve(spec, parameter)["in"] in {"path", "query", "header"}
    assert len(identities) == len(set(identities))
    for schema in spec["components"]["schemas"].values():
        Draft202012Validator.check_schema(schema)

    def check_refs(value):
        if isinstance(value, dict):
            if "$ref" in value:
                resolve(spec, value)
            for child in value.values():
                check_refs(child)
        elif isinstance(value, list):
            for child in value:
                check_refs(child)

    check_refs(spec)
