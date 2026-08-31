# SPDX-License-Identifier: AGPL-3.0-or-later
"""Utility portal links must follow the app's configured base URL."""

from unittest.mock import patch

from flask import Flask

import utility_portal


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
