# SPDX-License-Identifier: AGPL-3.0-or-later
"""Session guards on the dashboard routes that manage operator credentials.

These routes mint, rotate and revoke bearer tokens. Each one must refuse a
request without the session CSRF token and a session that is not a confirmed
administrator of the community, before any store call happens.
"""

from unittest.mock import patch

import pytest

import operator_api

COMMUNITY = "community-a"
ADMIN = {"building_id": "building-1", "status": "confirmed", "role": "admin"}

STATE_CHANGING = [
    ("post", f"/leg/community/{COMMUNITY}/operator-api/credentials"),
    ("get", f"/leg/community/{COMMUNITY}/operator-api/credentials"),
    ("post", f"/leg/community/{COMMUNITY}/operator-api/credentials/client-1/rotate"),
    ("post", f"/leg/community/{COMMUNITY}/operator-api/credentials/client-1/revoke"),
    ("post", f"/leg/community/{COMMUNITY}/operator-api/deliveries/delivery-1/retry"),
]
ADMIN_ONLY = [
    *STATE_CHANGING,
    ("get", f"/leg/community/{COMMUNITY}/operator-api/deliveries"),
]
STORE_CALLS = (
    "create_operator_api_client",
    "list_operator_api_clients",
    "rotate_operator_api_client",
    "revoke_operator_api_client",
    "retry_operator_webhook_delivery",
    "list_operator_webhook_deliveries",
)
CREATE_BODY = {"name": "Integrator", "capabilities": ["formation.read"]}


@pytest.fixture
def store():
    patches = {name: patch(f"operator_api.db.{name}") for name in STORE_CALLS}
    mocks = {name: p.start() for name, p in patches.items()}
    yield mocks
    for p in patches.values():
        p.stop()


def _client(app, member, csrf="csrf-secret"):
    if "operator_api" not in app.blueprints:
        app.register_blueprint(operator_api.operator_api_bp)
    app.config["SECRET_KEY"] = "credential-guard-key"
    client = app.test_client()
    with client.session_transaction() as session:
        session["dashboard_building_id"] = "building-1"
        session["dashboard_csrf_token"] = "csrf-secret"
    status = {"members": [member]} if member else {"members": []}
    patcher = patch(
        "operator_api.formation_wizard.get_community_status", return_value=status
    )
    patcher.start()
    return client, patcher, {"X-CSRF-Token": csrf} if csrf is not None else {}


def _send(client, method, path, headers):
    return getattr(client, method)(path, headers=headers, json=CREATE_BODY)


def _assert_no_store_call(store):
    called = [name for name, mock in store.items() if mock.called]
    assert called == []


@pytest.mark.parametrize(("method", "path"), STATE_CHANGING)
@pytest.mark.parametrize("csrf", [None, "", "wrong-token"])
def test_credential_routes_reject_a_missing_or_wrong_csrf_token(
    app, store, method, path, csrf
):
    client, patcher, headers = _client(app, ADMIN, csrf=csrf)
    try:
        response = _send(client, method, path, headers)
    finally:
        patcher.stop()

    assert response.status_code == 400
    assert response.get_json()["error"] == "Invalid CSRF token"
    _assert_no_store_call(store)


@pytest.mark.parametrize(("method", "path"), ADMIN_ONLY)
@pytest.mark.parametrize(
    "member",
    [
        None,
        {"building_id": "building-1", "status": "confirmed", "role": "member"},
        {
            "building_id": "building-1",
            "status": "confirmed",
            "access_roles": ["billing_preparer"],
        },
        {"building_id": "building-1", "status": "invited", "role": "admin"},
        {"building_id": "building-2", "status": "confirmed", "role": "admin"},
    ],
    ids=["no-member", "member", "billing-preparer", "unconfirmed-admin", "other"],
)
def test_credential_routes_refuse_anyone_but_a_confirmed_administrator(
    app, store, method, path, member
):
    client, patcher, headers = _client(app, member)
    try:
        response = _send(client, method, path, headers)
    finally:
        patcher.stop()

    assert response.status_code == 403
    _assert_no_store_call(store)


@pytest.mark.parametrize(("method", "path"), ADMIN_ONLY)
def test_a_confirmed_administrator_with_the_csrf_token_reaches_the_store(
    app, store, method, path
):
    store["create_operator_api_client"].return_value = {
        "id": "client-1",
        "webhook_secret": "olwhsec_x",
    }
    store["rotate_operator_api_client"].return_value = {
        "id": "client-1",
        "webhook_secret": "olwhsec_x",
    }
    store["revoke_operator_api_client"].return_value = {"id": "client-1"}
    store["list_operator_api_clients"].return_value = []
    store["retry_operator_webhook_delivery"].return_value = {"delivery_id": "d"}
    store["list_operator_webhook_deliveries"].return_value = []
    client, patcher, headers = _client(app, ADMIN)
    try:
        response = _send(client, method, path, headers)
    finally:
        patcher.stop()

    assert response.status_code in {200, 201}
    assert sum(mock.call_count for mock in store.values()) == 1


@pytest.mark.parametrize(("method", "path"), STATE_CHANGING)
def test_a_non_ascii_csrf_token_is_rejected_not_a_server_error(
    app, store, method, path
):
    client, patcher, _ = _client(app, ADMIN)
    try:
        response = client.open(
            path,
            method=method.upper(),
            data={"csrf_token": "csrf-sécret", "name": "Integrator"},
        )
    finally:
        patcher.stop()

    assert response.status_code == 400
    assert response.get_json()["error"] == "Invalid CSRF token"
    _assert_no_store_call(store)
