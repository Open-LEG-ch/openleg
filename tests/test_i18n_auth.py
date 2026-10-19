# SPDX-License-Identifier: AGPL-3.0-or-later
"""Session rotation keeps only a validated presentation-language preference."""

from unittest.mock import MagicMock

import pytest

from tests import test_i18n

app_module = test_i18n.app_module
isolated_environment = test_i18n.isolated_environment
web = test_i18n.web

# Imported route seams also read configuration on a cold Python process.
with test_i18n._synthetic_environment():
    import app


def test_french_survives_login_exchange_and_logout_without_old_identity(
    web, monkeypatch
):
    consume = MagicMock(return_value={"building_id": "new-building"})
    revoke = MagicMock()
    monkeypatch.setattr(app.db, "consume_dashboard_access_token", consume)
    monkeypatch.setattr(app.db, "revoke_dashboard_access_tokens", revoke)
    client = web.test_client()
    assert client.get("/login?lang=fr-CH").status_code == 200
    with client.session_transaction() as state:
        state["dashboard_building_id"] = "old-building"
        state["dashboard_csrf_token"] = "old-csrf"
        state["stale_secret"] = "must-disappear"
    response = client.get("/dashboard/access/" + "a" * 43)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    with client.session_transaction() as state:
        assert state["locale"] == "fr_CH"
        assert state["dashboard_building_id"] == "new-building"
        csrf = state["dashboard_csrf_token"]
        assert csrf and csrf != "old-csrf"
        assert "stale_secret" not in state
    assert client.get("/login").headers["Content-Language"] == "fr-CH"

    response = client.post("/dashboard/logout", data={"csrf_token": csrf})
    assert response.status_code == 302
    revoke.assert_called_once_with("new-building")
    with client.session_transaction() as state:
        assert dict(state) == {"locale": "fr_CH"}
    # The redirect's landing page stays French and the identity is gone.
    landing = client.get(response.headers["Location"])
    assert landing.status_code == 200
    assert landing.headers["Content-Language"] == "fr-CH"
    assert client.get("/dashboard/invoices").status_code == 401
    consume.assert_called_once()


@pytest.mark.parametrize("locale", ["../../fr", "it", {"unexpected": "fr"}])
def test_login_rotation_discards_invalid_locale_and_stale_auth_state(
    web, monkeypatch, locale
):
    monkeypatch.setattr(
        app.db,
        "consume_dashboard_access_token",
        lambda _: {"building_id": "new-building"},
    )
    client = web.test_client()
    with client.session_transaction() as state:
        state["locale"] = locale
        state["dashboard_building_id"] = "stale-building"
        state["dashboard_csrf_token"] = "stale-csrf"
        state["unexpected_secret"] = "discard"
    response = client.get("/dashboard/access/" + "a" * 43)
    assert response.status_code == 302
    with client.session_transaction() as state:
        assert "locale" not in state
        assert "unexpected_secret" not in state
        assert state["dashboard_building_id"] == "new-building"
        assert state["dashboard_csrf_token"] != "stale-csrf"
    assert client.get("/login").headers["Content-Language"] == "de-CH"


def test_logout_discards_invalid_locale_and_all_authentication_state(web, monkeypatch):
    revoke = MagicMock()
    monkeypatch.setattr(app.db, "revoke_dashboard_access_tokens", revoke)
    client = web.test_client()
    with client.session_transaction() as state:
        state["locale"] = "unsupported"
        state["dashboard_building_id"] = "building-1"
        state["dashboard_csrf_token"] = "csrf"
        state["stale_secret"] = "discard"
    assert (
        client.post("/dashboard/logout", data={"csrf_token": "csrf"}).status_code == 302
    )
    with client.session_transaction() as state:
        assert dict(state) == {}
    revoke.assert_called_once_with("building-1")
