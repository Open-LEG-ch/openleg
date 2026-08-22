# SPDX-License-Identifier: AGPL-3.0-or-later
"""Product API resilience contracts."""

import importlib
import os
from unittest.mock import MagicMock, patch

import pytest
from werkzeug.exceptions import TooManyRequests

import security_utils


@pytest.fixture
def full_app_module():
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
        import app as app_module

        app_module = importlib.reload(app_module)
        app_module.web = app_module.create_app(load_environment=False)
        yield app_module


def test_municipality_search_filters_by_name(client):
    profiles = [
        {"bfs_number": 4021, "name": "Baden", "kanton": "AG"},
        {"bfs_number": 261, "name": "Zürich", "kanton": "ZH"},
    ]
    with patch("api_public.db") as mock_db:
        mock_db.get_all_municipality_profiles.return_value = profiles
        response = client.get("/api/v1/municipalities?search=bad")

    assert response.status_code == 200
    assert [row["name"] for row in response.get_json()["municipalities"]] == ["Baden"]


def test_rate_limit_handler_returns_german_json(full_app_module):
    application = full_app_module.web
    handler = next(iter(application.error_handler_spec[None][429].values()))
    with application.test_request_context("/api/check_potential"):
        response = application.make_response(handler(TooManyRequests()))

    assert response.status_code == 429
    assert response.is_json
    assert "Anfragen" in response.get_json()["error"]


@pytest.mark.parametrize(
    "address",
    [
        "Rue de l'Hôpital 2, 2000 Neuchâtel",
        "Rue du Rhône 1, 1204 Genève",
        "Chemin du Château 5, 1095 Lutry",
        "Bahnhofstrasse 1, 8001 Zürich",
    ],
)
def test_address_validation_accepts_real_swiss_addresses(address):
    is_valid, _sanitized, error = security_utils.validate_address(address)

    assert is_valid, f"legitimate Swiss address rejected: {address!r} ({error})"


def test_address_suggestion_upstream_failure_returns_503(full_app_module):
    with patch.object(
        full_app_module.data_enricher, "get_address_suggestions", return_value=None
    ):
        response = full_app_module.web.test_client().get(
            "/api/suggest_addresses?q=Mellingerstrasse"
        )

    assert response.status_code == 503
    assert "verfügbar" in response.get_json()["error"]


def test_genuinely_empty_address_suggestions_stay_200(full_app_module):
    with patch.object(
        full_app_module.data_enricher, "get_address_suggestions", return_value=[]
    ):
        response = full_app_module.web.test_client().get(
            "/api/suggest_addresses?q=Atlantisweg"
        )

    assert response.status_code == 200
    assert response.get_json() == {"suggestions": []}


def test_address_enricher_signals_upstream_error(monkeypatch):
    import data_enricher

    def fail(*_args, **_kwargs):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(data_enricher.requests, "get", fail)

    assert data_enricher.get_address_suggestions("Mellingerstrasse 12") is None
