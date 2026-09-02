# SPDX-License-Identifier: AGPL-3.0-or-later
"""Product API resilience contracts."""

import importlib
import os
from unittest.mock import MagicMock, patch

import pytest
from werkzeug.exceptions import TooManyRequests

import data_enricher
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


def test_address_enricher_signals_upstream_error(monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(data_enricher.requests, "get", fail)

    assert data_enricher.get_address_suggestions("Mellingerstrasse 12") is None


_ADDRESS_ROUTES = (
    ("website", "/api/suggest_addresses"),
    ("public", "/api/v1/address/suggest"),
)
_LONG_LABEL = "A" * 220


@pytest.mark.parametrize(("route", "path"), _ADDRESS_ROUTES)
@pytest.mark.parametrize(
    ("query", "upstream", "expected_status", "expected_payload", "calls_adapter"),
    (
        ("M", [], 200, {"suggestions": []}, False),
        ("Me", [], 200, {"suggestions": []}, True),
        ("Mellingen", [], 200, {"suggestions": []}, True),
        (
            "Mellingen",
            [
                {
                    "label": " <b>Bahnhofstrasse 1</b> ",
                    "lat": 47.4,
                    "lon": 8.5,
                    "plz": "8001",
                    "upstream_only": "drop me",
                },
                {"label": _LONG_LABEL, "lat": None, "lon": None, "plz": 8001},
            ],
            200,
            {
                "suggestions": [
                    {
                        "label": "Bahnhofstrasse 1",
                        "lat": 47.4,
                        "lon": 8.5,
                        "plz": "8001",
                    },
                    {
                        "label": "A" * 200,
                        "lat": None,
                        "lon": None,
                        "plz": 8001,
                    },
                ]
            },
            True,
        ),
        (
            "Mellingen",
            [
                None,
                "raw",
                {},
                {"label": 1},
                {"label": "  "},
                {"label": "<br>"},
                {"label": "Mellingen", "lat": 47.4, "lon": 8.3, "plz": 5507},
            ],
            200,
            {
                "suggestions": [
                    {"label": "Mellingen", "lat": 47.4, "lon": 8.3, "plz": 5507}
                ]
            },
            True,
        ),
        (
            "Mellingen",
            None,
            503,
            {"error": "Adressvorschläge sind derzeit nicht verfügbar."},
            True,
        ),
    ),
)
def test_address_routes_share_one_outcome_contract(
    monkeypatch,
    full_app_module,
    client,
    route,
    path,
    query,
    upstream,
    expected_status,
    expected_payload,
    calls_adapter,
):
    adapter = MagicMock(return_value=upstream)
    monkeypatch.setattr(data_enricher, "get_address_suggestions", adapter)
    route_client = full_app_module.web.test_client() if route == "website" else client

    response = route_client.get(path, query_string={"q": query})

    assert response.status_code == expected_status
    assert response.get_json() == expected_payload
    assert adapter.call_count == int(calls_adapter)


@pytest.mark.parametrize(
    ("route", "path", "query", "extra_query", "expected_limit", "expected_ranges"),
    (
        ("website", "/api/suggest_addresses", "Mell", {}, 15, [[8000, 8999]]),
        ("website", "/api/suggest_addresses", "Melli", {}, 10, [[8000, 8999]]),
        (
            "public",
            "/api/v1/address/suggest",
            "Mellingen",
            {"plz_range": "5000-5999"},
            10,
            [[5000, 5999]],
        ),
    ),
)
def test_address_routes_keep_their_selection_inputs(
    monkeypatch,
    full_app_module,
    client,
    route,
    path,
    query,
    extra_query,
    expected_limit,
    expected_ranges,
):
    adapter = MagicMock(return_value=[])
    monkeypatch.setattr(data_enricher, "get_address_suggestions", adapter)
    route_client = full_app_module.web.test_client() if route == "website" else client

    response = route_client.get(path, query_string={"q": query, **extra_query})

    assert response.status_code == 200
    adapter.assert_called_once_with(
        query, limit=expected_limit, plz_ranges=expected_ranges
    )


def test_public_address_docs_include_upstream_failure(client):
    response = client.get("/api/v1/docs")

    assert response.status_code == 200
    assert (
        '"503": { description: "Adressvorschläge sind derzeit nicht verfügbar." }'
        in response.get_data(as_text=True)
    )
