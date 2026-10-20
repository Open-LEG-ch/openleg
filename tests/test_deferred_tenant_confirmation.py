# SPDX-License-Identifier: AGPL-3.0-or-later
"""Registration callbacks keep presentation after the request has ended."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import g, has_app_context

from tests import test_i18n
from tests.test_registration_contract import valid_data

app_module = test_i18n.app_module
isolated_environment = test_i18n.isolated_environment


@pytest.mark.parametrize(
    "locale,subject_word", [("fr_CH", "confirmer"), ("de_CH", "bestätigen")]
)
def test_registration_thread_keeps_captured_tenant(
    app_module, monkeypatch, locale, subject_word
):
    callbacks = []

    class DeferredThread:
        def __init__(self, *, target, args, daemon):
            callbacks.append((target, args))

        def start(self):
            pass

    db = app_module.db
    for name, value in [
        ("save_building", True),
        ("track_event", None),
        ("get_referral_code", None),
        ("get_building_by_referral_code", None),
    ]:
        monkeypatch.setattr(db, name, Mock(return_value=value))
    monkeypatch.setattr(
        db,
        "get_building",
        lambda _: {"email": "user@example.ch", "preferred_locale": locale},
    )
    for name, value in [
        ("check_request_size", (True, None)),
        ("validate_email_address", (True, "user@example.ch", None)),
        ("validate_phone", (True, "+41791234567", None)),
        ("validate_building_id", (True, None)),
        ("validate_coordinates", (True, None)),
    ]:
        monkeypatch.setattr(app_module.security_utils, name, Mock(return_value=value))
    monkeypatch.setattr(app_module, "find_provisional_matches", lambda _: None)
    monkeypatch.setattr(app_module, "collect_building_locations", lambda **kwargs: [])
    send = Mock()
    monkeypatch.setattr(app_module, "send_email", send)
    web = app_module.create_app(
        {
            "TESTING": True,
            "APP_BASE_URL": "https://lab.example.invalid",
            "RATELIMIT_STORAGE_URI": "memory://",
        },
        load_environment=False,
        check_database=False,
    )

    monkeypatch.setattr(app_module, "threading", SimpleNamespace(Thread=DeferredThread))

    @web.before_request
    def synthetic_tenant():
        g.tenant = {
            "territory": "synthetic-lake",
            "platform_name": "Synthetic Lake",
            "city_name": "Synthetic Commune",
        }

    response = web.test_client().post(
        "/api/register_full", json=valid_data(), base_url="https://localhost"
    )
    assert response.status_code == 200
    assert len(callbacks) == 1
    assert not has_app_context()
    target, args = callbacks[0]
    target(*args)
    email, subject, body = send.call_args.args
    assert email == "user@example.ch"
    assert "Synthetic Lake" in subject
    assert subject_word in subject
    assert "Synthetic Commune" in body
    assert "https://lab.example.invalid/confirm/" in body
    assert "lang=" + locale.replace("_", "-") in body
    assert "Zürich" not in body
