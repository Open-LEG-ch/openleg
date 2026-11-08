# SPDX-License-Identifier: AGPL-3.0-or-later
"""Admin credentials are accepted only as headers and never copied to redirects."""

import pytest

from tests import security_review_fixtures

app_module = security_review_fixtures.app_module


@pytest.mark.parametrize("transport", ["query", "form", "query-with-header"])
def test_admin_rejects_url_and_form_credentials(app_module, monkeypatch, transport):
    monkeypatch.setenv("ADMIN_TOKEN", "private-admin-credential")
    response = app_module.web.test_client().post(
        "/admin/registry/7/approve",
        query_string={"token": "private-admin-credential"}
        if transport.startswith("query")
        else {},
        data={"token": "private-admin-credential"} if transport == "form" else {},
        headers={"X-Admin-Token": "private-admin-credential"}
        if transport == "query-with-header"
        else {},
    )
    assert response.status_code in (400, 403)


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_admin_header_authorizes_a_clean_redirect(app_module, monkeypatch, action):
    monkeypatch.setenv("ADMIN_TOKEN", "private-admin-credential")
    monkeypatch.setattr(
        app_module.db, "update_registry_entry_moderation", lambda *args: True
    )
    response = app_module.web.test_client().post(
        f"/admin/registry/7/{action}",
        headers={"X-Admin-Token": "private-admin-credential"},
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/admin/ops"
    assert "private-admin-credential" not in response.text
