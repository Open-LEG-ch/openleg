# SPDX-License-Identifier: AGPL-3.0-or-later
"""Utility portal registration and link contracts."""

from unittest.mock import Mock, patch

import pytest
from flask import Flask

import utility_portal

REGISTRATION = {
    "company_name": "Netz Zürich",
    "contact_name": "Ada Muster",
    "contact_email": "ada@example.ch",
    "kanton": "zh",
}


def _registration_client(monkeypatch, *, saved):
    app = Flask(__name__)
    app.config.update(APP_BASE_URL="https://openleg.example", TESTING=True)
    app.register_blueprint(utility_portal.utility_bp)

    save = Mock(return_value=saved)
    track = Mock()
    send_magic_link = Mock()
    monkeypatch.setattr(
        utility_portal.db, "get_utility_client_by_email", Mock(return_value=None)
    )
    monkeypatch.setattr(utility_portal.db, "save_utility_client", save)
    monkeypatch.setattr(utility_portal.db, "track_event", track)
    monkeypatch.setattr(utility_portal, "_send_magic_link", send_magic_link)
    monkeypatch.setattr(
        utility_portal.security_utils,
        "validate_email_address",
        Mock(return_value=(True, REGISTRATION["contact_email"], None)),
    )
    return app.test_client(), save, track, send_magic_link


def _post_registration(client, request_format):
    if request_format == "json":
        return client.post("/utility/register", json=REGISTRATION)
    return client.post("/utility/register", data=REGISTRATION)


@pytest.mark.parametrize("request_format", ["json", "form"])
def test_registration_stops_after_failed_persistence(monkeypatch, request_format):
    client, save, track, send_magic_link = _registration_client(
        monkeypatch, saved=False
    )

    response = _post_registration(client, request_format)

    assert response.status_code == 503
    assert response.get_json() == {
        "error": "Wir konnten die Registrierung nicht speichern. Bitte versuchen Sie es später erneut."
    }
    save.assert_called_once()
    track.assert_not_called()
    send_magic_link.assert_not_called()


@pytest.mark.parametrize("request_format", ["json", "form"])
def test_registration_success_tracks_and_sends_one_link(monkeypatch, request_format):
    client, save, track, send_magic_link = _registration_client(monkeypatch, saved=True)

    response = _post_registration(client, request_format)

    if request_format == "json":
        assert response.status_code == 200
        assert response.get_json()["success"] is True
    else:
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/utility/login?registered=1")
    save.assert_called_once()
    track.assert_called_once()
    send_magic_link.assert_called_once()


def test_magic_link_follows_the_apps_configured_base_url():
    app = Flask(__name__)
    app.config["APP_BASE_URL"] = "https://from-config.example"

    with (
        app.app_context(),
        patch.object(utility_portal.db, "set_utility_magic_token"),
        patch.object(utility_portal, "send_email") as send_email,
    ):
        utility_portal._send_magic_link("client-1", "person@example.ch")

    send_email.assert_called_once()
    _to, _subject, body = send_email.call_args[0]
    assert "https://from-config.example/utility/login?token=" in body


def test_register_page_renders_the_apps_configured_base_url():
    app = Flask(__name__)
    app.config["APP_BASE_URL"] = "https://from-config.example"

    captured = {}
    with (
        app.test_request_context("/utility/register"),
        patch.object(
            utility_portal,
            "render_template",
            lambda _template, **context: captured.update(context) or "",
        ),
    ):
        utility_portal.register_page()

    assert captured["site_url"] == "https://from-config.example"
