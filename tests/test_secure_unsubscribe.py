# SPDX-License-Identifier: AGPL-3.0-or-later
"""Security contracts for mailbox-confirmed profile deletion (#291)."""

from unittest.mock import MagicMock

import pytest

from tests.test_app_organic_routes import _disable_rate_limit_hooks


@pytest.fixture
def app_module():
    import app as imported_app

    web = imported_app.create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "unsubscribe-test-key",
            "APP_BASE_URL": "http://localhost:5003",
            "RATELIMIT_STORAGE_URI": "memory://",
        },
        load_environment=False,
        check_database=False,
    )
    hooks = _disable_rate_limit_hooks(web)
    try:
        yield imported_app, web
    finally:
        web.before_request_funcs[None] = hooks


def test_post_sends_confirmation_without_deleting(app_module, monkeypatch):
    imported_app, web = app_module
    monkeypatch.setattr(
        imported_app.db,
        "get_building_by_email",
        MagicMock(return_value=[{"building_id": "building-1"}]),
    )
    save_token = MagicMock(return_value=True)
    delete_building = MagicMock()
    send_email = MagicMock()
    monkeypatch.setattr(imported_app.db, "save_token", save_token)
    monkeypatch.setattr(imported_app.db, "delete_building", delete_building)
    monkeypatch.setattr(imported_app, "send_email", send_email)

    response = web.test_client().post(
        "/unsubscribe", data={"email": "person@example.ch"}
    )

    assert response.status_code == 200
    assert "Bestätigungslink" in response.get_data(as_text=True)
    delete_building.assert_not_called()
    assert save_token.call_args.args[1:] == ("building-1", "unsubscribe")
    assert save_token.call_args.kwargs == {"ttl_seconds": 3600}
    message = send_email.call_args.args[2]
    assert "http://localhost:5003/unsubscribe/" in message


def test_post_response_does_not_reveal_if_email_exists(app_module, monkeypatch):
    imported_app, web = app_module
    monkeypatch.setattr(imported_app, "send_email", MagicMock())
    client = web.test_client()

    monkeypatch.setattr(imported_app.db, "get_building_by_email", lambda _email: [])
    unknown = client.post("/unsubscribe", data={"email": "unknown@example.ch"})

    monkeypatch.setattr(
        imported_app.db,
        "get_building_by_email",
        lambda _email: [{"building_id": "building-1"}],
    )
    monkeypatch.setattr(imported_app.db, "save_token", lambda *_args, **_kwargs: True)
    known = client.post("/unsubscribe", data={"email": "known@example.ch"})

    assert unknown.get_data(as_text=True) == known.get_data(as_text=True)


def test_token_requires_unsubscribe_type(app_module, monkeypatch):
    imported_app, web = app_module
    token = "12345678-1234-1234-1234-123456789abc"
    monkeypatch.setattr(
        imported_app.db,
        "get_token",
        lambda _token: {"building_id": "building-1", "token_type": "verification"},
    )
    use_token = MagicMock()
    delete_building = MagicMock()
    monkeypatch.setattr(imported_app.db, "use_token", use_token)
    monkeypatch.setattr(imported_app.db, "delete_building", delete_building)

    response = web.test_client().get(f"/unsubscribe/{token}")

    assert response.status_code == 404
    use_token.assert_not_called()
    delete_building.assert_not_called()


def test_token_get_only_renders_confirmation(app_module, monkeypatch):
    imported_app, web = app_module
    token = "12345678-1234-1234-1234-123456789abc"
    monkeypatch.setattr(
        imported_app.db,
        "get_token",
        lambda _token: {"building_id": "building-1", "token_type": "unsubscribe"},
    )
    confirm_deletion = MagicMock()
    monkeypatch.setattr(imported_app.db, "confirm_profile_deletion", confirm_deletion)

    response = web.test_client().get(f"/unsubscribe/{token}")

    assert response.status_code == 200
    assert "Löschung bestätigen" in response.get_data(as_text=True)
    confirm_deletion.assert_not_called()


def test_token_post_deletes_atomically(app_module, monkeypatch):
    imported_app, web = app_module
    token = "12345678-1234-1234-1234-123456789abc"
    monkeypatch.setattr(
        imported_app.db,
        "get_token",
        lambda _token: {"building_id": "building-1", "token_type": "unsubscribe"},
    )
    confirm_deletion = MagicMock(return_value=True)
    monkeypatch.setattr(imported_app.db, "confirm_profile_deletion", confirm_deletion)

    response = web.test_client().post(f"/unsubscribe/{token}")

    assert response.status_code == 200
    assert "erfolgreich gelöscht" in response.get_data(as_text=True)
    confirm_deletion.assert_called_once_with(token)


def test_failed_atomic_deletion_does_not_report_success(app_module, monkeypatch):
    imported_app, web = app_module
    token = "12345678-1234-1234-1234-123456789abc"
    monkeypatch.setattr(
        imported_app.db,
        "get_token",
        lambda _token: {"building_id": "building-1", "token_type": "unsubscribe"},
    )
    monkeypatch.setattr(
        imported_app.db, "confirm_profile_deletion", lambda _token: False
    )

    response = web.test_client().post(f"/unsubscribe/{token}")

    assert response.status_code == 409
    assert "nicht gelöscht" in response.get_data(as_text=True)


@pytest.mark.parametrize(
    "token",
    (
        pytest.param("not-a-uuid", id="not-a-uuid"),
        pytest.param("../../etc/passwd", id="traversal"),
        pytest.param("12345678-1234-1234-1234-12345678901", id="one-digit-short"),
        pytest.param("g2345678-1234-1234-1234-123456789012", id="non-hex"),
    ),
)
def test_a_malformed_token_is_a_404_and_never_reaches_the_database(
    app_module, monkeypatch, token
):
    """Every token in the tests above is well formed, so this branch never ran."""
    imported_app, web = app_module
    get_token = MagicMock()
    monkeypatch.setattr(imported_app.db, "get_token", get_token)

    response = web.test_client().get(f"/unsubscribe/{token}")

    assert response.status_code == 404
    get_token.assert_not_called()
