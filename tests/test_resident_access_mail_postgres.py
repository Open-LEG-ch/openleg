# SPDX-License-Identifier: AGPL-3.0-or-later
"""Resident mail follows its bound profile, including outside request context."""

import re
from urllib.parse import urlsplit

import pytest

from tests import test_dashboard_access_routes, test_interest_postgres

app_module = test_dashboard_access_routes.app_module
interest_database = test_interest_postgres.interest_database
pytestmark = pytest.mark.integration


def profile(db, locale, *, verified):
    identifier = "SYNTHETIC-MAIL-" + locale
    email = locale.lower() + "@example.com"
    assert db.save_building(
        identifier,
        email,
        {"address": "Synthetic private address", "lat": 47.2, "lon": 8.2},
        {},
        verified=verified,
        preferred_locale=locale,
    )
    return identifier, email


@pytest.mark.parametrize("locale", ["fr_CH", "de_CH"])
def test_confirmation_mail_uses_saved_language_without_flask_context(
    interest_database, app_module, monkeypatch, locale
):
    db = interest_database
    identifier, email = profile(db, locale, verified=False)
    captured = []
    monkeypatch.setattr(
        app_module, "send_email", lambda *args, **kwargs: captured.append(args)
    )
    app_module.send_confirmation_email(
        email,
        "https://example.invalid/confirm/synthetic-confirmation",
        identifier,
        "Synthetic private address",
    )
    assert len(captured) == 1
    recipient, subject, body = captured[0]
    assert recipient == email
    assert ("confirmer" if locale == "fr_CH" else "Interessenmeldung") in subject
    assert "lang=" + locale.replace("_", "-") in body
    assert "Synthetic private address" not in body
    assert db.get_building(identifier)["preferred_locale"] == locale


@pytest.mark.parametrize("locale", ["fr_CH", "de_CH"])
def test_access_request_cannot_select_the_recipients_language(
    interest_database, app_module, monkeypatch, locale
):
    db = interest_database
    identifier, email = profile(db, locale, verified=True)
    captured = []
    monkeypatch.setattr(
        app_module, "send_email", lambda *args, **kwargs: captured.append(args)
    )
    client = app_module.web.test_client()
    caller_locale = "de-CH" if locale == "fr_CH" else "fr-CH"
    response = client.post(
        "/dashboard/access/request?lang=" + caller_locale,
        data={"email": email, "preferred_locale": caller_locale},
    )
    assert response.status_code == 200
    assert email not in response.text
    assert len(captured) == 1
    recipient, subject, body = captured[0]
    assert recipient == email
    assert ("tableau de bord" if locale == "fr_CH" else "Dashboard") in subject
    assert "lang=" + locale.replace("_", "-") in body
    assert db.get_building(identifier)["preferred_locale"] == locale
    link = re.search(r"http[^\s]+/dashboard/access/[^\s]+", body)[0]
    parsed = urlsplit(link)
    exchanged = client.get(parsed.path + "?" + parsed.query)
    assert exchanged.status_code == 302
    with client.session_transaction() as state:
        assert state["dashboard_building_id"] == identifier
        assert state["locale"] == locale


def test_confirmation_does_not_borrow_another_email_profiles_preference(
    interest_database, app_module, monkeypatch
):
    identifier, _email = profile(interest_database, "fr_CH", verified=False)
    captured = []
    monkeypatch.setattr(
        app_module, "send_email", lambda *args, **kwargs: captured.append(args)
    )
    app_module.send_confirmation_email(
        "other@example.invalid",
        "https://example.invalid/confirm/synthetic-confirmation",
        identifier,
    )
    assert "Interessenmeldung" in captured[0][1]
