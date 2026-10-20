# SPDX-License-Identifier: AGPL-3.0-or-later
"""Owner-only language settings through the existing GRD portal."""

import re
from unittest.mock import Mock

import pytest

from tests import test_interest_postgres
from tests.test_i18n import _synthetic_environment

interest_database = test_interest_postgres.interest_database
pytestmark = pytest.mark.integration


def test_settings_persist_only_current_owners_locale_and_next_mail(
    interest_database, monkeypatch
):
    db = interest_database
    for identity in ["owner", "other"]:
        assert db.save_utility_client(
            identity,
            identity + " grid",
            identity + "@example.invalid",
            contact_name="Synthetic Contact",
            preferred_locale="de_CH",
        )
    before = db.get_utility_client("owner")
    other_before = db.get_utility_client("other")
    with _synthetic_environment():
        import app
        import utility_portal

        web = app.create_app(
            {"TESTING": True, "RATELIMIT_STORAGE_URI": "memory://"},
            load_environment=False,
            check_database=False,
        )
    client = web.test_client()
    with client.session_transaction() as session:
        session["utility_client_id"] = "owner"
    response = client.get("/utility/settings")
    assert response.status_code == 200
    token = re.search(r'name="csrf_token" value="([^"]+)"', response.text)[1]
    for data in [{}, {"csrf_token": "wrong"}, {"csrf_token": "é"}]:
        assert (
            client.post(
                "/utility/settings", data=data | {"preferred_locale": "fr-CH"}
            ).status_code
            == 403
        )
        assert db.get_utility_client("owner")["preferred_locale"] == "de_CH"
    assert (
        client.post(
            "/utility/settings", data={"csrf_token": token, "preferred_locale": "it-CH"}
        ).status_code
        == 400
    )
    response = client.post(
        "/utility/settings",
        data={
            "csrf_token": token,
            "preferred_locale": "fr-CH",
            "client_id": "other",
            "company_name": "injected",
        },
    )
    assert response.status_code == 302
    after = db.get_utility_client("owner")
    assert after["preferred_locale"] == "fr_CH"
    for key in before.keys() - {"preferred_locale", "updated_at"}:
        assert after[key] == before[key], key
    assert db.get_utility_client("other") == other_before
    # Persistence, not request language, controls the next outbound login.
    send = Mock()
    monkeypatch.setattr(utility_portal, "send_email", send)
    with web.test_request_context("/utility/login?lang=de-CH"):
        utility_portal._send_magic_link("owner", "owner@example.invalid")
    assert "Votre lien" in send.call_args.args[1]
    assert "lang=fr-CH" in send.call_args.args[2]
    anonymous = web.test_client()
    assert (
        anonymous.post(
            "/utility/settings", data={"csrf_token": token, "preferred_locale": "de_CH"}
        ).status_code
        == 302
    )
    assert db.get_utility_client("owner")["preferred_locale"] == "fr_CH"
