# SPDX-License-Identifier: AGPL-3.0-or-later
"""Utility credential lifecycle through authenticated HTTP and PostgreSQL."""

import hashlib
import re

import pytest

import database as db
from tests import test_store_billing_postgres
from tests.test_i18n import _synthetic_environment

billing_database = test_store_billing_postgres.billing_database
pytestmark = pytest.mark.integration


@pytest.fixture
def portal(billing_database):
    for identity in ("owner", "other"):
        assert db.save_utility_client(
            identity,
            identity + " grid",
            identity + "@example.org",
        )
        assert db.update_utility_client_api_key(identity, identity + "-old-hash")
    with _synthetic_environment():
        from app import create_app

        app = create_app(
            {"TESTING": True, "RATELIMIT_STORAGE_URI": "memory://"},
            load_environment=False,
            check_database=False,
        )
    return app


def _client(portal, identity="owner"):
    client = portal.test_client()
    with client.session_transaction() as session:
        session["utility_client_id"] = identity
    return client


def test_current_utility_can_revoke_its_key_without_touching_another_client(portal):
    client = _client(portal)
    other = db.get_utility_client("other")
    token = _csrf(client)
    response = client.post(
        "/utility/api-key/revoke",
        data={"csrf_token": token, "client_id": "other"},
    )
    assert response.status_code == 302
    assert response.location.endswith("/utility/dashboard")
    assert db.get_utility_client("owner")["api_key_hash"] is None
    assert db.get_utility_client("other") == other
    page = client.get(response.location)
    assert "Noch kein API-Schlüssel vorhanden." in page.text
    assert (
        client.post(
            "/utility/api-key/revoke",
            data={"csrf_token": token},
        ).status_code
        == 302
    )


def _csrf(client):
    page = client.get("/utility/dashboard")
    assert page.status_code == 200
    return re.search(r'name="csrf_token" value="([^"]+)"', page.text)[1]


def test_creation_replacement_and_revocation_report_only_persisted_private_state(
    portal, caplog
):
    client = _client(portal)
    token = _csrf(client)
    other = db.get_utility_client("other")
    keys = []
    for _ in range(2):
        response = client.post(
            "/utility/api-key",
            headers={"X-CSRF-Token": token},
            json={"client_id": "other"},
        )
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        raw = response.json["api_key"]
        assert raw.startswith("oleg_")
        assert (
            db.get_utility_client("owner")["api_key_hash"]
            == hashlib.sha256(raw.encode()).hexdigest()
        )
        assert raw not in client.get("/utility/dashboard").text
        assert response.location is None
        assert raw not in caplog.text
        keys.append(raw)
    assert keys[0] != keys[1]
    assert db.get_utility_client("other") == other
    assert (
        client.post("/utility/api-key/revoke", data={"csrf_token": token}).status_code
        == 302
    )
    assert db.get_utility_client("owner")["api_key_hash"] is None
    assert db.get_utility_client("other") == other
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT data::text FROM analytics_events WHERE event_type LIKE 'utility_api_key_%'"
        )
        events = str(cur.fetchall())
        assert all(raw not in events for raw in keys)


@pytest.mark.parametrize("path", ["/utility/api-key", "/utility/api-key/revoke"])
@pytest.mark.parametrize("supplied", [None, "wrong", "é"])
def test_credential_writes_reject_missing_wrong_and_non_ascii_csrf(
    portal, path, supplied
):
    client = _client(portal)
    _csrf(client)
    before = [db.get_utility_client(identity) for identity in ("owner", "other")]
    data = {} if supplied is None else {"csrf_token": supplied}
    assert client.post(path, data=data).status_code == 403
    assert [
        db.get_utility_client(identity) for identity in ("owner", "other")
    ] == before


def test_tokens_from_another_session_do_not_authorize_credential_changes(portal):
    first, second = _client(portal), _client(portal, "other")
    first_token, second_token = _csrf(first), _csrf(second)
    assert first_token != second_token
    before = db.get_utility_client("other")
    for path in ("/utility/api-key", "/utility/api-key/revoke"):
        assert second.post(path, data={"csrf_token": first_token}).status_code == 403
    assert db.get_utility_client("other") == before


@pytest.mark.parametrize("path", ["/utility/api-key", "/utility/api-key/revoke"])
def test_anonymous_and_missing_clients_cannot_change_credentials(portal, path):
    before = [db.get_utility_client(identity) for identity in ("owner", "other")]
    for client in (portal.test_client(), _client(portal, "nonexistent")):
        response = client.post(
            path, data={"csrf_token": "anything", "client_id": "owner"}
        )
        assert response.status_code == 302
        assert response.location.endswith("/utility/login")
    assert [
        db.get_utility_client(identity) for identity in ("owner", "other")
    ] == before


@pytest.mark.parametrize("path", ["/utility/api-key", "/utility/api-key/revoke"])
def test_failed_database_writes_do_not_report_success_or_change_the_key(portal, path):
    client = _client(portal)
    token = _csrf(client)
    before = db.get_utility_client("owner")
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("""CREATE FUNCTION reject_credential_write() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'synthetic credential write failure'; END $$""")
        cur.execute("""CREATE TRIGGER reject_credential_write BEFORE UPDATE ON utility_clients
            FOR EACH ROW WHEN (OLD.client_id='owner') EXECUTE FUNCTION reject_credential_write()""")
    response = client.post(path, data={"csrf_token": token})
    assert response.status_code == 503
    if response.is_json:
        assert (
            response.json["error"] == "Der API-Schlüssel konnte nicht geändert werden."
        )
        assert "api_key" not in response.json
    else:
        assert "Der API-Schlüssel konnte nicht geändert werden." in response.text
    assert db.get_utility_client("owner") == before


@pytest.mark.parametrize("path", ["/utility/api-key", "/utility/api-key/revoke"])
def test_stale_account_token_cannot_modify_the_next_signed_in_client(portal, path):
    client = _client(portal)
    old_token = _csrf(client)
    owner = db.get_utility_client("owner")
    before_login = db.get_utility_client("other")
    assert client.get("/utility/logout").status_code == 302
    assert db.set_utility_magic_token("other", "fictional-other-login", 900)
    login = client.get("/utility/login?token=fictional-other-login")
    assert login.status_code == 302
    other = db.get_utility_client("other")
    assert other["api_key_hash"] == before_login["api_key_hash"]
    # Do not load the new dashboard first: the old tab must already be denied.
    assert client.post(path, data={"csrf_token": old_token}).status_code == 403
    assert db.get_utility_client("other") == other
    token = _csrf(client)
    assert token != old_token
    assert client.post(path, data={"csrf_token": old_token}).status_code == 403
    response = client.post(path, data={"csrf_token": token})
    assert response.status_code == (200 if path == "/utility/api-key" else 302)
    assert db.get_utility_client("owner") == owner
    assert db.get_utility_client("other")["api_key_hash"] != other["api_key_hash"]
