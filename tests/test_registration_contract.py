# SPDX-License-Identifier: AGPL-3.0-or-later
"""HTTP contract tests for both registration routes."""

import importlib
import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(scope="module")
def app_module():
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

        app = importlib.reload(app)
        if app.limiter:
            app.limiter.enabled = False
        hooks = list(app.app.before_request_funcs.get(None, []))
        app.app.before_request_funcs[None] = [
            hook
            for hook in hooks
            if not (
                getattr(hook, "__module__", "").startswith("flask_limiter")
                or getattr(hook, "__name__", "") == "_check_request_limit"
            )
        ]
        yield app
        app.app.before_request_funcs[None] = hooks


@pytest.fixture
def registration(app_module, monkeypatch):
    threads = []

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            self.target = target
            self.args = args
            self.daemon = daemon
            self.started = False
            threads.append(self)

        def start(self):
            self.started = True

    db = app_module.db
    for name in (
        "get_building_by_referral_code",
        "save_building",
        "save_token",
        "track_event",
        "get_referral_code",
    ):
        monkeypatch.setattr(db, name, MagicMock())
    db.get_referral_code.return_value = "REFER"

    monkeypatch.setattr(
        app_module.security_utils,
        "check_request_size",
        MagicMock(return_value=(True, None)),
    )
    monkeypatch.setattr(
        app_module.security_utils,
        "validate_email_address",
        MagicMock(return_value=(True, "user@example.ch", None)),
    )
    monkeypatch.setattr(
        app_module.security_utils,
        "validate_phone",
        MagicMock(return_value=(True, "+41791234567", None)),
    )
    monkeypatch.setattr(
        app_module.security_utils,
        "validate_building_id",
        MagicMock(return_value=(True, None)),
    )
    monkeypatch.setattr(
        app_module.security_utils,
        "validate_coordinates",
        MagicMock(return_value=(True, None)),
    )
    monkeypatch.setattr(app_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(app_module, "send_confirmation_email", MagicMock())
    monkeypatch.setattr(app_module, "run_full_ml_task", MagicMock())
    monkeypatch.setattr(app_module, "find_provisional_matches", MagicMock())
    app_module.find_provisional_matches.return_value = None
    monkeypatch.setattr(
        app_module,
        "collect_building_locations",
        MagicMock(return_value=[{"lat": 47.1, "lon": 8.1, "type": "anonymous"}]),
    )
    monkeypatch.setattr(
        app_module.email_automation, "schedule_sequence_for_user", MagicMock()
    )

    return app_module.app.test_client(), db, threads


def valid_data(**updates):
    data = {
        "email": " user@example.ch ",
        "phone": " 079 123 45 67 ",
        "profile": {
            "building_id": "building-1",
            "lat": 47.1,
            "lon": 8.1,
            "address": "Badenerstrasse 1",
        },
        "consents": {
            "share_with_neighbors": True,
            "share_with_utility": True,
        },
    }
    data.update(updates)
    return data


@pytest.mark.parametrize("url", ["/api/register_anonymous", "/api/register_full"])
def test_registration_empty_json_contract(registration, url):
    client, _, _ = registration
    response = client.post(url, json={})
    assert response.status_code == 400
    assert response.get_json() == {"error": "Keine Daten empfangen."}


@pytest.mark.parametrize("url", ["/api/register_anonymous", "/api/register_full"])
def test_registration_request_size_contract(registration, app_module, url):
    client, _, _ = registration
    app_module.security_utils.check_request_size.return_value = (False, "Zu gross.")
    response = client.post(url, json=valid_data())
    assert response.status_code == 413
    assert response.get_json() == {"error": "Zu gross."}


@pytest.mark.parametrize("url", ["/api/register_anonymous", "/api/register_full"])
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("email", "Ungültige E-Mail."),
        ("phone", "Ungültige Telefonnummer."),
        ("profile", "Profildaten fehlen."),
        ("building_id", "Ungültige Gebäude-ID."),
        ("coordinates", "Ungültige Koordinaten."),
        ("consents", "Bitte stimmen Sie der Datenweitergabe zu."),
    ],
)
def test_registration_400_contracts(registration, app_module, url, case, message):
    client, _, _ = registration
    data = valid_data()
    if case == "email":
        app_module.security_utils.validate_email_address.return_value = (
            False,
            None,
            message,
        )
    elif case == "phone":
        app_module.security_utils.validate_phone.return_value = (False, None, message)
    elif case == "profile":
        data["profile"] = None
    elif case == "building_id":
        app_module.security_utils.validate_building_id.return_value = (False, message)
    elif case == "coordinates":
        app_module.security_utils.validate_coordinates.return_value = (False, message)
    else:
        data["consents"]["share_with_utility"] = False

    response = client.post(url, json=data)
    assert response.status_code == 400
    assert response.get_json() == {"error": message}


@pytest.mark.parametrize(
    ("url", "user_type"),
    [
        ("/api/register_anonymous", "anonymous"),
        ("/api/register_full", "registered"),
    ],
)
@pytest.mark.parametrize("matched", [False, True])
def test_registration_happy_path_contract(
    registration, app_module, url, user_type, matched
):
    client, db, threads = registration
    cluster = {"community_id": 7} if matched else None
    app_module.find_provisional_matches.return_value = cluster
    db.get_building_by_referral_code.return_value = {"building_id": "referrer-1"}

    response = client.post(url, json=valid_data(referral_code=" KNOWN "))

    assert response.status_code == 200
    payload = response.get_json()
    assert set(payload) == {
        "buildings",
        "match_found",
        "verification_email_sent",
        "referral_link",
    } | ({"cluster_info"} if matched else set())
    assert payload["match_found"] is matched
    assert payload["referral_link"] == "http://localhost:5003/?ref=REFER"
    assert payload.get("cluster_info") == cluster
    db.get_building_by_referral_code.assert_called_once_with("KNOWN")
    db.save_building.assert_called_once_with(
        building_id="building-1",
        email="user@example.ch",
        profile=valid_data()["profile"],
        consents=db.save_building.call_args.kwargs["consents"],
        user_type=user_type,
        phone="+41791234567",
        referrer_id="referrer-1",
        city_id="zurich",
    )
    db.track_event.assert_called_once_with(
        "registration", "building-1", {"type": user_type, "city_id": "zurich"}
    )
    assert [
        (thread.target, thread.args, thread.daemon, thread.started)
        for thread in threads
    ] == [
        (
            app_module.send_confirmation_email,
            (
                "user@example.ch",
                db.save_token.call_args.args[0].join(
                    ["http://localhost:5003/unsubscribe/", ""]
                ),
                "building-1",
                "Badenerstrasse 1",
            ),
            True,
            True,
        ),
        (app_module.run_full_ml_task, ("building-1", "zurich"), True, True),
        (
            app_module.email_automation.schedule_sequence_for_user,
            ("building-1", "user@example.ch"),
            True,
            True,
        ),
    ]


@pytest.mark.parametrize("url", ["/api/register_anonymous", "/api/register_full"])
def test_registration_unknown_referral_is_ignored(registration, url):
    client, db, _ = registration
    db.get_building_by_referral_code.return_value = None
    response = client.post(url, json=valid_data(referral_code="UNKNOWN"))
    assert response.status_code == 200
    assert db.save_building.call_args.kwargs["referrer_id"] is None
