# SPDX-License-Identifier: AGPL-3.0-or-later
"""Credential limit responses through the dashboard HTTP boundary."""

from unittest.mock import patch

from flask import Flask

import database as db
import operator_api


def admin_client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["dashboard_building_id"] = "admin-1"
        session["dashboard_csrf_token"] = "csrf"
    return client


def credential_app():
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="test")
    app.register_blueprint(operator_api.operator_api_bp)
    return app


ADMIN_STATUS = {
    "members": [{"building_id": "admin-1", "role": "admin", "status": "confirmed"}]
}
URL = "/leg/community/cap-leg/operator-api/credentials"
HEADERS = {"X-CSRF-Token": "csrf"}
PAYLOAD = {
    "name": "Integration",
    "capabilities": ["formation.read"],
    "actor_kind": "service",
}
ERROR = {
    "error": "Credential limit reached",
    "schema_version": "operator-api/1",
    "code": "conflict",
    "retryable": False,
}


def test_creation_limit_returns_fixed_conflict_without_secrets():
    with (
        patch(
            "operator_api.formation_wizard.get_community_status",
            return_value=ADMIN_STATUS,
        ),
        patch(
            "operator_api.db.create_operator_api_client",
            side_effect=db.OperatorCredentialLimitReached("private details"),
        ),
    ):
        response = admin_client(credential_app()).post(
            URL, json=PAYLOAD, headers=HEADERS
        )
    assert response.status_code == 409
    assert response.get_json() == ERROR
    assert response.headers["Cache-Control"] == "no-store"
