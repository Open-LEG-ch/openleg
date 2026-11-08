# SPDX-License-Identifier: AGPL-3.0-or-later
"""Browser moderation uses an expiring, revocable session and CSRF checks."""

from unittest.mock import Mock

import pytest

from tests import security_review_fixtures

app_module = security_review_fixtures.app_module


@pytest.fixture
def browser(app_module, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "synthetic-admin-credential")
    for name in (
        "get_ops_snapshots",
        "get_lea_reports",
        "list_registry_entries",
        "get_registry_entries_needing_verification",
        "list_sdat_ingestion_schedules",
        "get_operator_interest_records",
    ):
        monkeypatch.setattr(app_module.db, name, lambda **kwargs: [])
    monkeypatch.setattr(app_module.db, "get_registry_pending_count", lambda: 0)
    monkeypatch.setattr(app_module.db, "get_operator_interest_counts", dict)
    moderation = Mock(return_value=True)
    monkeypatch.setattr(app_module.db, "update_registry_entry_moderation", moderation)
    return app_module.web.test_client(), moderation


def login(client):
    response = client.post(
        "/admin/session", headers={"X-Admin-Token": "synthetic-admin-credential"}
    )
    assert response.status_code == 302
    return response


def test_browser_login_keeps_the_secret_out_of_the_page_and_redirect(browser):
    client, _ = browser
    page = client.get("/admin/login")
    assert page.status_code == 200
    assert page.headers["Cache-Control"] == "no-store"
    assert 'type="password"' in page.text
    assert "synthetic-admin-credential" not in page.text
    response = login(client)
    assert response.status_code == 302
    assert response.location == "/admin/ops"
    with client.session_transaction() as session:
        assert "synthetic-admin-credential" not in str(dict(session))
    assert (
        client.get(response.location, headers={"Accept": "text/html"}).status_code
        == 200
    )


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_browser_moderation_requires_csrf_before_writing(browser, action):
    client, moderation = browser
    login(client)
    path = f"/admin/registry/7/{action}"
    assert client.post(path).status_code == 403
    assert client.post(path, data={"csrf_token": "wrong"}).status_code == 403
    assert client.post(path, data={"csrf_token": "falsch-ü"}).status_code == 403
    moderation.assert_not_called()
    with client.session_transaction() as session:
        csrf = session["admin_csrf"]
    response = client.post(path, data={"csrf_token": csrf, "reason": "duplicate"})
    assert response.status_code == 302
    assert client.get(response.location).status_code == 200
    moderation.assert_called_once_with(
        7,
        "published" if action == "approve" else "rejected",
        "" if action == "approve" else "duplicate",
    )


def test_admin_cookie_does_not_authorize_machine_endpoints(browser):
    client, _ = browser
    login(client)
    assert client.get("/api/email/stats").status_code == 403


@pytest.mark.parametrize("change", ["expired", "rotated", "disabled"])
def test_admin_cookie_expires_and_tracks_credential_rotation(
    browser, monkeypatch, change
):
    client, _ = browser
    login(client)
    if change == "expired":
        with client.session_transaction() as session:
            session["admin_expires"] = 0
    else:
        monkeypatch.setenv(
            "ADMIN_TOKEN", "different-token" if change == "rotated" else ""
        )
    assert client.get("/admin/ops").status_code in (403, 404)


def test_login_requires_the_header_and_logout_requires_csrf(browser):
    client, _ = browser
    assert (
        client.post(
            "/admin/session", data={"token": "synthetic-admin-credential"}
        ).status_code
        == 400
    )
    assert client.post("/admin/session").status_code == 403
    assert login(client).status_code == 302
    assert client.post("/admin/logout").status_code == 403
    with client.session_transaction() as session:
        csrf = session["admin_csrf"]
    assert client.post("/admin/logout", data={"csrf_token": csrf}).status_code == 302
    assert client.get("/admin/ops").status_code == 403


def test_header_moderation_can_follow_its_redirect(browser):
    client, moderation = browser
    response = client.post(
        "/admin/registry/7/approve",
        headers={"X-Admin-Token": "synthetic-admin-credential"},
    )
    assert response.status_code == 302
    assert client.get(response.location).status_code == 200
    moderation.assert_called_once()


def test_login_attempts_are_rate_limited(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "synthetic-admin-credential")
    web = security_review_fixtures.make_web()
    responses = [web.test_client().post("/admin/session") for _ in range(6)]
    assert [response.status_code for response in responses] == [403] * 5 + [429]


def test_moderation_page_contains_session_bound_csrf_fields(
    browser, app_module, monkeypatch
):
    client, _ = browser
    monkeypatch.setattr(
        app_module.db, "list_registry_entries", lambda **kwargs: [{"id": 7}]
    )
    login(client)
    response = client.get("/admin/ops", headers={"Accept": "text/html"})
    assert response.status_code == 200
    with client.session_transaction() as session:
        csrf = session["admin_csrf"]
    assert response.text.count(f'name="csrf_token" value="{csrf}"') == 3


def test_invalid_header_does_not_fall_back_to_browser_session(browser):
    client, _ = browser
    login(client)
    assert (
        client.get("/admin/ops", headers={"X-Admin-Token": "incorrect"}).status_code
        == 403
    )
