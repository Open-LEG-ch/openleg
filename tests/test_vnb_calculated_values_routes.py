# SPDX-License-Identifier: AGPL-3.0-or-later
"""Safe operator surface for calculated-value delivery outcomes."""

from unittest.mock import patch

from app import create_app


def test_operator_import_and_listing_are_private(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    application = create_app(
        {"TESTING": True, "RATELIMIT_STORAGE_URI": "memory://"},
        load_environment=False,
        check_database=False,
    )
    outcome = {
        "status": "partially_invalid",
        "diagnostics": [{"code": "incomplete_period"}],
        "record_count": 3,
    }
    with (
        patch("vnb_calculated_values.accept_delivery", return_value=outcome) as accept,
        patch("database.list_calculated_values_deliveries", return_value=[outcome]),
    ):
        client = application.test_client()
        response = client.post(
            "/admin/vnb-calculated-values/dietikon",
            headers={"X-Admin-Token": "secret"},
            json={"transport": "file", "delivery": {"territory": "other"}},
        )
        listing = client.get(
            "/admin/vnb-calculated-values/dietikon",
            headers={"X-Admin-Token": "secret"},
        )
    assert response.status_code == 422
    assert response.get_json() == outcome
    assert response.headers["Cache-Control"] == "no-store"
    assert listing.get_json() == {"deliveries": [outcome]}
    delivered = accept.call_args.args[0]
    assert delivered["territory"] == "dietikon"
    assert accept.call_args.kwargs["transport"] == "file"


def test_operator_surface_requires_admin(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    application = create_app(
        {"TESTING": True, "RATELIMIT_STORAGE_URI": "memory://"},
        load_environment=False,
        check_database=False,
    )
    assert (
        application.test_client()
        .get("/admin/vnb-calculated-values/dietikon")
        .status_code
        == 403
    )


def test_operator_surface_rejects_non_object_json(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    application = create_app(
        {"TESTING": True, "RATELIMIT_STORAGE_URI": "memory://"},
        load_environment=False,
        check_database=False,
    )
    response = application.test_client().post(
        "/admin/vnb-calculated-values/dietikon",
        headers={"X-Admin-Token": "secret"},
        json=[],
    )
    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid_payload"}
