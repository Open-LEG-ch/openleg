# SPDX-License-Identifier: AGPL-3.0-or-later
"""Notifications at the verified municipality-interest seam."""

import importlib
import os
from unittest.mock import MagicMock, patch

import email_automation


def _app_module():
    with (
        patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql://x:x@localhost/x",
                "REDIS_URL": "memory://",
                "CRON_SECRET": "test-cron-secret",
                "APP_BASE_URL": "http://localhost:5003",
            },
        ),
        patch("database.is_db_available", return_value=True),
        patch("database._connection_pool", MagicMock()),
    ):
        import app

        module = importlib.reload(app)
        return module, module.create_app(load_environment=False)


def test_verified_newcomer_notifies_each_existing_municipality_recipient_once(
    monkeypatch,
):
    monkeypatch.setattr(
        email_automation.db,
        "get_verified_interest_recipients",
        MagicMock(
            return_value=[
                "eins@example.ch",
                "zwei@example.ch",
                "eins@example.ch",
                "neu@example.ch",
            ]
        ),
        raising=False,
    )
    monkeypatch.setattr(
        email_automation.db,
        "get_interest_counts_by_bfs",
        MagicMock(return_value={2554: 3}),
    )
    send = MagicMock(return_value=True)
    monkeypatch.setattr(email_automation, "_send_email", send)

    result = email_automation.notify_new_municipality_interest(
        bfs_number=2554,
        municipality_name="Riedholz",
        newcomer_email="neu@example.ch",
    )

    assert result == {"sent": 2, "failed": 0}
    assert [call.args[0] for call in send.call_args_list] == [
        "eins@example.ch",
        "zwei@example.ch",
    ]
    for call in send.call_args_list:
        recipient, subject, body = call.args
        assert recipient not in body
        assert "neu@example.ch" not in body
        assert "Riedholz" in subject
        assert "3 bestätigte Interessierte" in body


def test_new_interest_notification_uses_configured_unsubscribe_url(monkeypatch):
    monkeypatch.setattr(
        email_automation.db,
        "get_verified_interest_recipients",
        MagicMock(return_value=["eins@example.ch"]),
    )
    monkeypatch.setattr(
        email_automation.db,
        "get_interest_counts_by_bfs",
        MagicMock(return_value={2554: 2}),
    )
    send = MagicMock(return_value=True)
    monkeypatch.setattr(email_automation, "_send_email", send)

    email_automation.notify_new_municipality_interest(
        bfs_number=2554,
        municipality_name="Riedholz",
        newcomer_email="neu@example.ch",
        base_url="https://riedholz.openleg.ch/",
    )

    assert "https://riedholz.openleg.ch/unsubscribe" in send.call_args.args[2]


def test_confirmation_link_makes_interest_visible_and_notifies_existing_user(
    monkeypatch,
):
    app_module, application = _app_module()
    token = "12345678-1234-4234-8234-123456789012"
    monkeypatch.setattr(
        app_module.db,
        "get_token",
        MagicMock(
            return_value={
                "token": token,
                "token_type": "verification",
                "building_id": "new-building",
            }
        ),
    )
    monkeypatch.setattr(app_module.db, "use_token", MagicMock(return_value=True))
    monkeypatch.setattr(
        app_module.db, "update_building_verified", MagicMock(return_value=True)
    )
    monkeypatch.setattr(
        app_module.db,
        "get_building",
        MagicMock(
            return_value={
                "building_id": "new-building",
                "email": "neu@example.ch",
                "address": "Ahornstrasse 1, 4533 Riedholz",
                "bfs_number": 2554,
                "municipality_name": "Riedholz",
            }
        ),
    )
    monkeypatch.setattr(
        app_module.db,
        "get_verified_interest_recipients",
        MagicMock(return_value=["bestehend@example.ch"]),
    )
    monkeypatch.setattr(
        app_module.db,
        "get_interest_counts_by_bfs",
        MagicMock(return_value={2554: 2}),
    )
    send = MagicMock(return_value=True)
    monkeypatch.setattr(email_automation, "_send_email", send)

    response = application.test_client().get(f"/confirm/{token}")

    assert response.status_code == 200
    assert "bestätigt" in response.get_data(as_text=True)
    assert send.call_args.args[0] == "bestehend@example.ch"
    assert "Ahornstrasse" not in send.call_args.args[2]


def test_unresolved_address_can_be_submitted_for_email_verification(monkeypatch):
    app_module, application = _app_module()
    saved = MagicMock(return_value=True)
    monkeypatch.setattr(app_module.db, "save_coverage_request", saved)
    monkeypatch.setattr(
        app_module.db,
        "search_municipality_profiles",
        MagicMock(
            return_value=[{"bfs_number": 2554, "name": "Riedholz", "kanton": "SO"}]
        ),
    )
    sent = MagicMock(return_value=True)
    monkeypatch.setattr(app_module, "send_email", sent)

    response = application.test_client().post(
        "/api/register_interest",
        json={
            "email": "person@example.ch",
            "address": "Ahornstrasse 99",
            "plz": "4533",
            "municipality_name": "Riedholz",
            "roles": ["owner", "solar_producer"],
            "has_solar": True,
        },
    )

    assert response.status_code == 202
    assert response.get_json() == {
        "accepted": True,
        "message": "Bitte bestätigen Sie Ihre E-Mail-Adresse.",
    }
    stored = saved.call_args.kwargs
    assert stored["bfs_number"] == 2554
    assert stored["municipality_name"] == "Riedholz"
    assert stored["roles"] == ["owner", "solar_producer"]
    assert stored["has_solar"] is True
    assert "annual_consumption_kwh" not in stored
    assert "potential_pv_kwp" not in stored
    assert f"/interest/confirm/{stored['verification_token']}" in sent.call_args.args[2]


def test_coverage_confirmation_counts_and_notifies_without_exposing_address(
    monkeypatch,
):
    app_module, application = _app_module()
    token = "coverage-token"
    monkeypatch.setattr(
        app_module.db,
        "verify_coverage_request",
        MagicMock(
            return_value={
                "email": "neu@example.ch",
                "bfs_number": 2554,
                "municipality_name": "Riedholz",
            }
        ),
    )
    monkeypatch.setattr(
        app_module.db,
        "get_verified_interest_recipients",
        MagicMock(return_value=["bestehend@example.ch"]),
    )
    monkeypatch.setattr(
        app_module.db,
        "get_interest_counts_by_bfs",
        MagicMock(return_value={2554: 2}),
    )
    send = MagicMock(return_value=True)
    monkeypatch.setattr(email_automation, "_send_email", send)

    response = application.test_client().get(f"/interest/confirm/{token}")

    assert response.status_code == 200
    assert "bestätigt" in response.get_data(as_text=True)
    assert send.call_args.args[0] == "bestehend@example.ch"
    assert "neu@example.ch" not in send.call_args.args[2]


def test_homepage_explains_nationwide_verified_interest_funnel():
    _module, application = _app_module()

    html = application.test_client().get("/").get_data(as_text=True)

    assert "Adressen in der ganzen Schweiz" in html
    assert "sobald weitere bestätigte Interessierte" in html
    assert 'id="interest-fallback"' in html
    assert 'id="interest-plz"' in html
    assert 'id="interest-municipality"' in html
    assert 'name="interest-role"' in html
    assert 'id="has-solar"' in html
    assert 'id="consent-utility"' not in html
    assert "Netzbetreiber und Netzebene" in html


def test_interest_cleanup_runs_behind_the_cron_secret(monkeypatch):
    app_module, application = _app_module()
    cleanup = MagicMock(
        return_value={"coverage_requests_deleted": 4, "buildings_deleted": 2}
    )
    monkeypatch.setattr(app_module.db, "cleanup_expired_interest", cleanup)

    denied = application.test_client().post("/api/cron/cleanup-interest")
    accepted = application.test_client().post(
        "/api/cron/cleanup-interest",
        headers={"X-Cron-Secret": "test-cron-secret"},
    )

    assert denied.status_code == 403
    assert accepted.status_code == 200
    assert accepted.get_json() == {
        "coverage_requests_deleted": 4,
        "buildings_deleted": 2,
    }
    cleanup.assert_called_once_with()
