# SPDX-License-Identifier: AGPL-3.0-or-later
"""HTTP contracts for private municipality dashboard access."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import access_token
from tests.test_app_organic_routes import _disable_rate_limit_hooks


@pytest.fixture
def app_module():
    import app as imported_app

    web = imported_app.create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "municipality-access-test-key",
            "APP_BASE_URL": "http://localhost:5003",
            "RATELIMIT_STORAGE_URI": "memory://",
        },
        load_environment=False,
        check_database=False,
    )
    hooks = _disable_rate_limit_hooks(web)
    try:
        imported_app.web = web
        yield imported_app
    finally:
        web.before_request_funcs[None] = hooks


def test_anonymous_query_identifier_cannot_open_municipality_dashboard(
    app_module, monkeypatch
):
    private_context = MagicMock(
        return_value={
            "municipality": {"id": 7, "name": "Vertrauliche Gemeinde"},
            "status_label": "Aktiv",
            "stats": {},
            "solar_score": None,
            "energy_score": None,
            "invite_url": "https://example.invalid",
            "error": None,
        }
    )
    import municipality

    monkeypatch.setattr(municipality.db, "get_municipality", MagicMock())
    monkeypatch.setattr(municipality, "_dashboard_context", private_context)

    response = app_module.web.test_client().get("/gemeinde/dashboard?bfs=4021")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'action="/gemeinde/access/request"' in html
    assert "Vertrauliche Gemeinde" not in html
    municipality.db.get_municipality.assert_not_called()
    private_context.assert_not_called()


def test_municipality_session_identity_selects_dashboard(app_module, monkeypatch):
    import municipality

    municipality_row = {"id": 7, "name": "Baden", "subdomain": "baden"}
    get_municipality = MagicMock(return_value=municipality_row)
    monkeypatch.setattr(municipality.db, "get_municipality", get_municipality)
    monkeypatch.setattr(
        municipality,
        "_dashboard_context",
        lambda row: {
            "municipality": row,
            "status_label": "Aktiv",
            "stats": {},
            "solar_score": None,
            "energy_score": None,
            "invite_url": "https://baden.openleg.ch",
            "error": None,
        },
    )
    client = app_module.web.test_client()
    with client.session_transaction() as state:
        state["municipality_id"] = 7

    response = client.get("/gemeinde/dashboard?bfs=9999&subdomain=attacker")

    assert response.status_code == 200
    assert "Baden" in response.get_data(as_text=True)
    get_municipality.assert_called_once_with(municipality_id=7)


def test_access_request_does_not_disclose_registered_email(app_module, monkeypatch):
    import municipality

    find_by_email = MagicMock(return_value=None)
    send_email = MagicMock(return_value=True)
    issue_token = MagicMock(return_value="a" * 43)
    monkeypatch.setattr(
        municipality.db,
        "get_municipality_by_admin_email",
        find_by_email,
        raising=False,
    )
    monkeypatch.setattr(access_token, "issue", issue_token)
    built_urls = []

    def build_url(kind, base_url, token):
        built_urls.append(kind)
        return f"{base_url}/gemeinde/access/{token}"

    monkeypatch.setattr(access_token, "access_url", build_url)
    monkeypatch.setattr(
        municipality,
        "email_utils",
        SimpleNamespace(send_email=send_email),
        raising=False,
    )
    client = app_module.web.test_client()

    unknown = client.post(
        "/gemeinde/access/request", data={"email": "unknown@example.ch"}
    )
    find_by_email.return_value = {"id": 7, "admin_email": "known@example.ch"}
    known = client.post("/gemeinde/access/request", data={"email": "known@example.ch"})

    assert unknown.status_code == known.status_code == 200
    message = "Falls eine Gemeinde zu dieser E-Mail-Adresse existiert"
    assert message in unknown.get_data(as_text=True)
    assert message in known.get_data(as_text=True)
    issue_token.assert_called_once_with(
        access_token.MUNICIPALITY, municipality.db, 7, ttl_seconds=900
    )
    send_email.assert_called_once()
    assert "/gemeinde/access/" in send_email.call_args.args[2]
    assert built_urls == [access_token.MUNICIPALITY]


def test_magic_link_is_single_use_and_creates_clean_session(app_module, monkeypatch):
    import municipality

    consume = MagicMock(side_effect=[7, None])
    consumed_kinds = []

    def consume_token(kind, repository, token):
        assert kind is access_token.MUNICIPALITY
        assert repository is municipality.db
        consumed_kinds.append(kind)
        return consume(token)

    monkeypatch.setattr(access_token, "consume", consume_token)
    token = "a" * 43
    client = app_module.web.test_client()

    accepted = client.get(f"/gemeinde/access/{token}")

    assert accepted.status_code == 302
    assert accepted.headers["Location"].endswith("/gemeinde/dashboard")
    assert token not in accepted.headers["Location"]
    assert "no-store" in accepted.headers["Cache-Control"]
    assert accepted.headers["Referrer-Policy"] == "no-referrer"
    with client.session_transaction() as state:
        assert state["municipality_id"] == 7
        assert state["municipality_csrf_token"]

    replay = app_module.web.test_client().get(f"/gemeinde/access/{token}")
    assert replay.status_code == 302
    assert replay.headers["Location"].endswith("/gemeinde/dashboard?access=invalid")
    assert consumed_kinds == [access_token.MUNICIPALITY, access_token.MUNICIPALITY]


def test_logout_requires_csrf_revokes_links_and_clears_session(app_module, monkeypatch):
    import municipality

    revoke = MagicMock(return_value=2)
    monkeypatch.setattr(
        municipality.db,
        "revoke_municipality_access_tokens",
        revoke,
    )
    client = app_module.web.test_client()
    with client.session_transaction() as state:
        state["municipality_id"] = 7
        state["municipality_csrf_token"] = "csrf-secret"

    rejected = client.post("/gemeinde/logout")
    accepted = client.post("/gemeinde/logout", data={"csrf_token": "csrf-secret"})

    assert rejected.status_code == 400
    assert accepted.status_code == 302
    assert accepted.headers["Location"].endswith("/")
    revoke.assert_called_once_with(7)
    with client.session_transaction() as state:
        assert "municipality_id" not in state
        assert "municipality_csrf_token" not in state


def test_access_request_is_limited_to_five_attempts_per_minute(monkeypatch):
    import app as imported_app
    import municipality

    monkeypatch.setattr(
        municipality.db, "get_municipality_by_admin_email", lambda _email: None
    )
    web = imported_app.create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "municipality-rate-limit-test-key",
            "APP_BASE_URL": "http://localhost:5003",
            "RATELIMIT_STORAGE_URI": "memory://",
        },
        load_environment=False,
        check_database=False,
    )
    imported_app.limiter.reset()
    client = web.test_client()

    responses = [
        client.post(
            "/gemeinde/access/request",
            data={"email": "unknown@example.ch"},
            environ_overrides={"REMOTE_ADDR": "192.0.2.81"},
        )
        for _attempt in range(6)
    ]

    assert [response.status_code for response in responses] == [200] * 5 + [429]
