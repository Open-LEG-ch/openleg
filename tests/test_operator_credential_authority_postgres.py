# SPDX-License-Identifier: AGPL-3.0-or-later
"""Credential lifecycle through HTTP and real PostgreSQL."""

from unittest.mock import patch

import pytest
from flask import Flask

from i18n import init_app
from tests import test_interest_postgres
from tests.test_operator_credential_authority import CREATOR, http_client

interest_database = test_interest_postgres.interest_database


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    init_app(app)
    return app


@pytest.mark.integration
def test_revoked_community_credential_cannot_be_rotated_or_used(app, interest_database):
    db = interest_database
    db.save_building(
        "admin-a",
        "admin@example.ch",
        {"address": "Testweg 1", "lat": 47.2, "lon": 8.2},
        {},
    )
    cid = db.create_community_record("Test LEG", "admin-a", "simple", "")
    client = http_client(app)
    headers = {"X-CSRF-Token": "csrf"}
    base = f"/leg/community/{cid}/operator-api/credentials"
    created = client.post(
        base,
        headers=headers,
        json={"name": "Operator", "capabilities": ["formation.read"]},
    )
    assert created.status_code == 201
    credential = created.json
    path = base + "/" + credential["credential"]["id"]
    # Active credentials can still rotate.
    rotated = client.post(path + "/rotate", headers=headers)
    assert rotated.status_code == 200
    assert rotated.json["token"] != credential["token"]
    assert client.post(path + "/revoke", headers=headers).status_code == 200
    denied = client.post(path + "/rotate", headers=headers)
    assert denied.status_code == 404
    assert denied.json == {
        "error": "Credential not found",
        "schema_version": "operator-api/1",
    }
    listed = client.get(base, headers=headers).json["credentials"]
    assert listed[0]["active"] is False
    for token in (credential["token"], rotated.json["token"]):
        assert (
            client.get(
                f"/api/operator/v1/communities/{cid}/formation",
                headers={"Authorization": f"Bearer {token}"},
            ).status_code
            == 401
        )


@pytest.mark.integration
def test_other_community_administrator_can_revoke_creator_credential(
    app, interest_database
):
    db = interest_database
    db.save_building(
        "admin-a",
        "admin@example.ch",
        {"address": "Testweg 1", "lat": 47.2, "lon": 8.2},
        {},
    )
    cid = db.create_community_record("Test LEG", "admin-a", "simple", "")
    client = http_client(app)
    base = f"/leg/community/{cid}/operator-api/credentials"
    headers = {"X-CSRF-Token": "csrf"}
    created = client.post(
        base,
        headers=headers,
        json={"name": "Operator", "capabilities": ["formation.read"]},
    ).json
    client = http_client(app, "other-admin")
    with patch(
        "operator_api.formation_wizard.get_community_status",
        return_value={"members": [{**CREATOR, "building_id": "other-admin"}]},
    ):
        response = client.post(
            base + "/" + created["credential"]["id"] + "/revoke", headers=headers
        )
    assert response.status_code == 200
    assert response.json["credential"]["active"] is False
    assert (
        client.get(
            f"/api/operator/v1/communities/{cid}/formation",
            headers={"Authorization": "Bearer " + created["token"]},
        ).status_code
        == 401
    )
