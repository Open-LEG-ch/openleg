# SPDX-License-Identifier: AGPL-3.0-or-later
"""Automated emails must issue short-lived dashboard access links."""

from unittest.mock import MagicMock

from flask import Flask

import access_token
import email_automation


def _pending_email():
    return {
        "id": 7,
        "building_id": "building-1",
        "email": "person@example.ch",
        "template_key": "day_0_welcome",
        "address": "Musterweg 1",
        "lat": None,
        "lon": None,
    }


def _patch_queue(monkeypatch):
    monkeypatch.setattr(
        email_automation.db, "get_pending_emails", lambda limit=50: [_pending_email()]
    )
    monkeypatch.setattr(
        email_automation.db, "get_referral_code", lambda _building_id: None
    )
    monkeypatch.setattr(email_automation.db, "mark_email_sent", MagicMock())
    monkeypatch.setattr(email_automation.db, "mark_email_failed", MagicMock())
    monkeypatch.setattr(
        email_automation,
        "_get_tenant_for_building",
        lambda _building_id: {
            "platform_name": "OpenLEG",
            "city_name": "Baden",
            "territory": "baden",
            "primary_color": "#1f3d32",
            "contact_email": "hallo@openleg.ch",
            "utility_name": "Regionalwerke AG Baden",
        },
    )


def test_queue_render_uses_fresh_magic_link_without_building_id(monkeypatch):
    _patch_queue(monkeypatch)
    issue = MagicMock(return_value="a" * 43)
    monkeypatch.setattr(access_token, "issue", issue)
    captured = {}
    monkeypatch.setattr(
        email_automation,
        "render_template",
        lambda _template, **context: captured.update(context) or "email body",
    )
    monkeypatch.setattr(email_automation, "_send_email", MagicMock(return_value=True))
    monkeypatch.setattr(email_automation, "APP_BASE_URL", "https://openleg.ch")
    app = Flask(__name__)
    app.config["DASHBOARD_EMAIL_TOKEN_TTL_SECONDS"] = 86_400

    result = email_automation.process_email_queue(app=app)

    assert result == {"sent": 1, "failed": 0, "total": 1}
    issue.assert_called_once_with(
        access_token.DASHBOARD,
        email_automation.db,
        "building-1",
        ttl_seconds=86_400,
    )
    assert captured["dashboard_url"] == (
        "https://openleg.ch/dashboard/access/" + "a" * 43
    )
    assert "bid=" not in captured["dashboard_url"]
    assert "building-1" not in captured["dashboard_url"]


def test_queue_render_prefers_the_apps_configured_base_url(monkeypatch):
    _patch_queue(monkeypatch)
    issue = MagicMock(return_value="a" * 43)
    monkeypatch.setattr(access_token, "issue", issue)
    captured = {}
    monkeypatch.setattr(
        email_automation,
        "render_template",
        lambda _template, **context: captured.update(context) or "email body",
    )
    monkeypatch.setattr(email_automation, "_send_email", MagicMock(return_value=True))
    monkeypatch.setattr(
        email_automation, "APP_BASE_URL", "https://module-default.example"
    )
    app = Flask(__name__)
    app.config["DASHBOARD_EMAIL_TOKEN_TTL_SECONDS"] = 86_400
    app.config["APP_BASE_URL"] = "https://from-config.example"

    result = email_automation.process_email_queue(app=app)

    assert result == {"sent": 1, "failed": 0, "total": 1}
    assert captured["dashboard_url"] == (
        "https://from-config.example/dashboard/access/" + "a" * 43
    )
    assert captured["site_url"] == "https://from-config.example"


def test_queue_render_strips_a_trailing_slash_from_the_configured_base_url(
    monkeypatch,
):
    _patch_queue(monkeypatch)
    monkeypatch.setattr(access_token, "issue", MagicMock(return_value="a" * 43))
    captured = {}
    monkeypatch.setattr(
        email_automation,
        "render_template",
        lambda _template, **context: captured.update(context) or "email body",
    )
    monkeypatch.setattr(email_automation, "_send_email", MagicMock(return_value=True))
    app = Flask(__name__)
    app.config["DASHBOARD_EMAIL_TOKEN_TTL_SECONDS"] = 86_400
    app.config["APP_BASE_URL"] = "https://from-config.example/"

    email_automation.process_email_queue(app=app)

    assert captured["unsubscribe_url"] == "https://from-config.example/unsubscribe"
    assert captured["site_url"] == "https://from-config.example"


def test_queue_fails_closed_when_access_token_cannot_be_issued(monkeypatch):
    _patch_queue(monkeypatch)
    monkeypatch.setattr(access_token, "issue", lambda *_args, **_kwargs: None)
    send = MagicMock(return_value=True)
    monkeypatch.setattr(email_automation, "_send_email", send)
    app = Flask(__name__)
    app.config["DASHBOARD_EMAIL_TOKEN_TTL_SECONDS"] = 86_400

    result = email_automation.process_email_queue(app=app)

    assert result == {"sent": 0, "failed": 1, "total": 1}
    send.assert_not_called()
    email_automation.db.mark_email_failed.assert_called_once_with(
        7, "Dashboard access token could not be issued"
    )
