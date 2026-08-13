# SPDX-License-Identifier: AGPL-3.0-or-later
"""Utility portal HTTP interface tests."""

from unittest.mock import patch

from flask import Flask

import utility_portal


def _client():
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="test-secret")
    app.register_blueprint(utility_portal.utility_bp)
    return app.test_client()


def test_registration_rejects_non_json_with_json_error():
    response = _client().post(
        "/utility/register", data="not-json", content_type="text/plain"
    )

    assert response.status_code == 400
    assert response.is_json
    assert response.get_json() == {"error": "Geben Sie Firmenname und E-Mail ein."}


def test_login_rejects_non_json_with_json_error():
    response = _client().post(
        "/utility/login", data="not-json", content_type="text/plain"
    )

    assert response.status_code == 400
    assert response.is_json
    assert response.get_json() == {"error": "E-Mail-Adresse ist erforderlich"}


def test_registration_rejects_non_object_json():
    response = _client().post("/utility/register", json=["invalid"])

    assert response.status_code == 400
    assert response.get_json() == {"error": "Geben Sie Firmenname und E-Mail ein."}


def test_login_rejects_non_string_email():
    response = _client().post("/utility/login", json={"email": ["invalid"]})

    assert response.status_code == 400
    assert response.get_json() == {"error": "E-Mail-Adresse ist erforderlich"}


def test_registration_rejects_invalid_phone_before_writing():
    with (
        patch.object(
            utility_portal.db, "get_utility_client_by_email", return_value=None
        ),
        patch.object(utility_portal.db, "save_utility_client") as save_client,
        patch.object(utility_portal, "_send_magic_link") as send_magic_link,
    ):
        response = _client().post(
            "/utility/register",
            json={
                "company_name": "Regionalwerke AG Baden",
                "contact_email": "kontakt@example.com",
                "contact_phone": "definitely-not-a-phone",
            },
        )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Geben Sie eine gültige Schweizer Telefonnummer ein."
    }
    save_client.assert_not_called()
    send_magic_link.assert_not_called()
