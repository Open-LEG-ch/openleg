# SPDX-License-Identifier: AGPL-3.0-or-later
"""Account mail follows stored recipient choices, independent of the requester."""

import os
from unittest.mock import MagicMock, patch

import pytest

from tests import test_i18n

with test_i18n._synthetic_environment():
    import access_token
    import leg_registry
    import municipality
    import registry_intake
    import utility_portal
    from tests import (
        test_i18n_utility,
        test_interest_postgres,
        test_municipality_access_routes,
    )

web = test_i18n_utility.web
interest_database = test_interest_postgres.interest_database
app_module = test_municipality_access_routes.app_module


@pytest.fixture(autouse=True)
def isolated_settings():
    # Keep native services and mutation controls while isolating app settings.
    controls = {
        key: os.environ[key]
        for key in ("DATABASE_URL", "MUTANT_UNDER_TEST", "MUTMUT_DEPENDENCY_DEPTH")
        if key in os.environ
    }
    with test_i18n._synthetic_environment(), patch.dict(os.environ, controls):
        yield


@pytest.mark.parametrize("matching", [True, False])
def test_municipality_mail_uses_bound_admin_preference(
    app_module, monkeypatch, matching
):
    monkeypatch.setattr(
        municipality.db,
        "get_municipality_by_admin_email",
        lambda _: {
            "id": 7,
            "admin_email": "admin@example.ch",
            "admin_building_id": "admin-building",
        },
    )
    monkeypatch.setattr(
        municipality.db,
        "get_building",
        lambda _: {
            "email": "admin@example.ch" if matching else "other@example.ch",
            "preferred_locale": "fr_CH",
        },
    )
    monkeypatch.setattr(access_token, "issue", lambda *a, **k: "a" * 43)
    send = MagicMock(return_value=True)
    monkeypatch.setattr(municipality.email_utils, "send_email", send)
    response = app_module.web.test_client().post(
        "/gemeinde/access/request?lang=de",
        data={"email": "admin@example.ch", "preferred_locale": "de_CH"},
    )
    assert response.status_code == 200
    recipient, subject, body = send.call_args.args
    assert recipient == "admin@example.ch"
    assert ("Votre lien" if matching else "Ihr Gemeinde") in subject
    assert ("Ouvrez" if matching else "Öffnen") in body
    assert "lang=" + ("fr-CH" if matching else "de-CH") in body
    assert "/gemeinde/access/" + "a" * 43 in body


@pytest.mark.parametrize(
    "stored,expected",
    [("fr_CH", "Votre lien"), ("de_CH", "Ihr Login"), ("invalid", "Ihr Login")],
)
def test_utility_mail_uses_persisted_preference(web, monkeypatch, stored, expected):
    monkeypatch.setattr(
        utility_portal.db,
        "get_utility_client",
        lambda _: {"contact_email": "contact@example.ch", "preferred_locale": stored},
    )
    monkeypatch.setattr(utility_portal.db, "set_utility_magic_token", MagicMock())
    send = MagicMock(return_value=True)
    monkeypatch.setattr(utility_portal, "send_email", send)
    with web.test_request_context("/utility/login?lang=fr"):
        utility_portal._send_magic_link("client-a", "contact@example.ch")
    assert expected in send.call_args.args[1]
    assert "15" in send.call_args.args[2]
    assert (
        "lang=" + ("fr-CH" if stored == "fr_CH" else "de-CH") in send.call_args.args[2]
    )


def test_utility_registration_saves_selected_language(web, monkeypatch):
    monkeypatch.setattr(
        utility_portal.db, "get_utility_client_by_email", lambda _: None
    )
    save = MagicMock(return_value=True)
    monkeypatch.setattr(utility_portal.db, "save_utility_client", save)
    monkeypatch.setattr(utility_portal.db, "track_event", MagicMock())
    monkeypatch.setattr(utility_portal, "_send_magic_link", MagicMock())
    response = web.test_client().post(
        "/utility/register?lang=fr",
        json={"company_name": "Synthetic", "contact_email": "contact@example.ch"},
    )
    assert response.status_code == 200
    assert save.call_args.kwargs["preferred_locale"] == "fr_CH"


def test_public_login_cannot_override_recipient_language(web, monkeypatch):
    row = {
        "client_id": "client-a",
        "contact_email": "contact@example.ch",
        "preferred_locale": "fr_CH",
    }
    monkeypatch.setattr(utility_portal.db, "get_utility_client_by_email", lambda _: row)
    monkeypatch.setattr(utility_portal.db, "get_utility_client", lambda _: row)
    monkeypatch.setattr(utility_portal.db, "set_utility_magic_token", MagicMock())
    send = MagicMock(return_value=True)
    save = MagicMock()
    monkeypatch.setattr(utility_portal, "send_email", send)
    monkeypatch.setattr(utility_portal.db, "save_utility_client", save)
    response = web.test_client().post(
        "/utility/login?lang=de",
        json={"email": "contact@example.ch", "preferred_locale": "de_CH"},
    )
    assert response.status_code == 200
    assert "Votre lien" in send.call_args.args[1]
    save.assert_not_called()


def test_unbound_utility_address_uses_german(web, monkeypatch):
    monkeypatch.setattr(
        utility_portal.db,
        "get_utility_client",
        lambda _: {"contact_email": "other@example.ch", "preferred_locale": "fr_CH"},
    )
    monkeypatch.setattr(utility_portal.db, "set_utility_magic_token", MagicMock())
    send = MagicMock(return_value=True)
    monkeypatch.setattr(utility_portal, "send_email", send)
    with web.test_request_context("/?lang=fr"):
        utility_portal._send_magic_link("client-a", "contact@example.ch")
    assert "Ihr Login" in send.call_args.args[1]


def test_registry_worker_localizes_each_entry_without_flask(monkeypatch):
    monkeypatch.setattr(
        leg_registry.db,
        "get_registry_entries_needing_verification",
        lambda **_: [
            {
                "id": 1,
                "name": "Synthetic A",
                "contact_email": "a@example.ch",
                "preferred_locale": "fr_CH",
            },
            {"id": 2, "name": "Synthetic B", "contact_email": "b@example.ch"},
        ],
    )
    monkeypatch.setattr(
        leg_registry.db, "set_registry_verification_token", lambda *a, **k: True
    )
    send = MagicMock(return_value=True)
    monkeypatch.setattr(leg_registry.email_utils, "send_email", send)
    assert leg_registry.send_verification_nudges("https://example.ch") == {
        "candidates": 2,
        "sent": 2,
        "errors": 0,
    }
    french, german = [call.args for call in send.call_args_list]
    assert french[0] == "a@example.ch" and "Votre inscription" in french[1]
    assert (
        "Synthetic A" in french[2]
        and "14 jours" in french[2]
        and "lang=fr-CH" in french[2]
    )
    assert german[0] == "b@example.ch" and "Ist Ihr LEG" in german[1]
    assert (
        "Synthetic B" in german[2]
        and "14 Tage" in german[2]
        and "lang=de-CH" in german[2]
    )


@pytest.mark.parametrize(
    "supplied,expected",
    [("fr-CH", "fr_CH"), ("de", "de_CH"), (None, "de_CH"), ("../../fr", "de_CH")],
)
def test_registry_submission_records_only_supported_language(
    monkeypatch, supplied, expected
):
    monkeypatch.setattr(
        registry_intake.db, "get_registry_entry_by_slug", lambda _: None
    )
    save = MagicMock(return_value={"id": 1})
    monkeypatch.setattr(registry_intake.db, "save_registry_entry", save)
    monkeypatch.setattr(registry_intake.db, "track_event", MagicMock())
    result = registry_intake.submit(
        {
            "name": "Synthetic",
            "contact_email": "contact@example.ch",
            "preferred_locale": supplied,
        },
        source="self_hosted",
    )
    assert result["error"] is None
    assert save.call_args.kwargs["preferred_locale"] == expected


@pytest.mark.integration
def test_account_mail_preferences_migrate_and_round_trip(interest_database):
    import database as db

    # Simulate existing tables, then run the guarded upgrade twice.
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "ALTER TABLE utility_clients DROP COLUMN IF EXISTS preferred_locale"
        )
        cur.execute("ALTER TABLE leg_registry DROP COLUMN IF EXISTS preferred_locale")
        cur.execute(
            "INSERT INTO utility_clients (client_id,company_name,contact_email) VALUES ('old-client','Synthetic','old@example.ch')"
        )
        cur.execute(
            "INSERT INTO leg_registry (slug,name,contact_email) VALUES ('old-entry','Synthetic','old@example.ch')"
        )
    db.create_tables()
    db.create_tables()
    assert db.get_utility_client("old-client")["preferred_locale"] == "de_CH"
    assert db.get_registry_entry_by_slug("old-entry")["preferred_locale"] == "de_CH"
    assert db.save_utility_client(
        "new-client", "Synthetic", "new@example.ch", preferred_locale="fr-CH"
    )
    assert db.get_utility_client("new-client")["preferred_locale"] == "fr_CH"
    assert db.save_utility_client("new-client", "Updated", "new@example.ch")
    assert db.get_utility_client("new-client")["preferred_locale"] == "fr_CH"
    row = db.save_registry_entry(
        "new-entry", "Synthetic", "new@example.ch", preferred_locale="fr-CH"
    )
    assert db.get_registry_entry(row["id"])["preferred_locale"] == "fr_CH"
    for save, args in [
        (db.save_utility_client, ("invalid-client", "Synthetic", "x@example.ch")),
        (db.save_registry_entry, ("invalid-entry", "Synthetic", "x@example.ch")),
    ]:
        with pytest.raises(ValueError, match="unsupported_recipient_locale"):
            save(*args, preferred_locale="../../fr")
