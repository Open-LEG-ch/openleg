# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sensitive production routes never disclose their URL to external resources."""

import pytest

from tests import test_i18n

app_module = test_i18n.app_module
isolated_environment = test_i18n.isolated_environment


@pytest.fixture
def web(app_module):
    return app_module.create_app(
        {"TESTING": True, "MATOMO_SITE_ID": "1", "RATELIMIT_STORAGE_URI": "memory://"},
        load_environment=False,
        check_database=False,
    )


@pytest.mark.parametrize("lang", ["de-CH", "fr-CH"])
@pytest.mark.parametrize(
    "path",
    [
        "/confirm/not-valid",
        "/interest/confirm/synthetic-token",
        "/unsubscribe/not-valid",
        "/registry/verify/synthetic-token",
        "/utility/login?token=synthetic-token",
        "/login?access_token=synthetic-token",
    ],
)
def test_sensitive_success_and_error_responses_are_private(
    web, monkeypatch, lang, path
):
    import database

    monkeypatch.setattr(
        database,
        "verify_coverage_request",
        lambda token: {"municipality_name": "Example", "email": "test@example.invalid"},
    )
    monkeypatch.setattr(
        database, "get_registry_entry_by_verification_token", lambda token: None
    )
    monkeypatch.setattr(
        database, "get_utility_client_by_magic_token", lambda token: None
    )
    monkeypatch.setattr(
        database, "consume_utility_magic_token", lambda token: None, raising=False
    )
    response = web.test_client().get(
        path + ("&" if "?" in path else "?") + "lang=" + lang
    )
    assert response.status_code in (200, 404)
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "https:" not in response.headers["Content-Security-Policy"]
    assert "stats.openleg.ch" not in response.text
    assert "fonts.googleapis.com" not in response.text
    assert "fonts.gstatic.com" not in response.text


def test_private_redirect_is_also_protected(web, monkeypatch):
    import database

    monkeypatch.setattr(
        database,
        "get_utility_client_by_magic_token",
        lambda token: {"client_id": "synthetic-client", "status": "active"},
    )
    monkeypatch.setattr(database, "clear_utility_magic_token", lambda client_id: None)
    monkeypatch.setattr(
        database,
        "consume_utility_magic_token",
        lambda token: {"client_id": "synthetic-client", "status": "active"},
        raising=False,
    )
    response = web.test_client().get("/utility/login?token=synthetic-token")
    assert response.status_code == 302
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "https:" not in response.headers["Content-Security-Policy"]


@pytest.mark.parametrize("path", ["/open-source", "/datenschutz"])
def test_ordinary_public_pages_retain_analytics(web, path):
    response = web.test_client().get(path)
    assert response.status_code == 200
    assert "https://stats.openleg.ch/matomo.js" in response.text
    assert "https://stats.openleg.ch" in response.headers["Content-Security-Policy"]


def test_authenticated_utility_response_is_private(web, monkeypatch):
    import database

    monkeypatch.setattr(database, "get_utility_client", lambda identifier: None)
    client = web.test_client()
    with client.session_transaction() as session:
        session["utility_client_id"] = "synthetic-client"
    response = client.get("/utility/settings")
    assert response.status_code == 302
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
