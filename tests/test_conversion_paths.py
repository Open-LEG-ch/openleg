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


def test_live_address_suggestions_filter_plz_strip_markup_and_keep_limit(monkeypatch):
    response = MagicMock()
    response.json.return_value = {
        "results": [
            {"attrs": {"label": "Outside 4000", "plz": 4000}},
            {"attrs": {"label": "No postal code", "plz": "invalid"}},
            {
                "attrs": {
                    "label": "<b>Bahnhofstrasse 1, 5507 Mellingen</b>",
                    "plz": "invalid",
                    "lat": 47.4,
                    "lon": 8.3,
                }
            },
            {
                "attrs": {
                    "label": "<i>Dorfstrasse 2, 5507 Mellingen</i>",
                    "lat": 47.5,
                    "lon": 8.4,
                }
            },
            {"attrs": {"label": "Must not pass the limit", "plz": 5507}},
        ]
    }
    monkeypatch.setattr(data_enricher.requests, "get", MagicMock(return_value=response))

    suggestions = data_enricher.get_address_suggestions(
        "Mellingen", limit=2, plz_ranges=[[5000, 5999]]
    )

    assert suggestions == [
        {
            "label": "Bahnhofstrasse 1, 5507 Mellingen",
            "lat": 47.4,
            "lon": 8.3,
            "plz": 5507,
        },
        {
            "label": "Dorfstrasse 2, 5507 Mellingen",
            "lat": 47.5,
            "lon": 8.4,
            "plz": 5507,
        },
    ]
    data_enricher.requests.get.assert_called_once_with(
        data_enricher.GEO_API_URL,
        params={"searchText": "Mellingen", "type": "locations", "limit": 6},
        timeout=5,
    )
    response.raise_for_status.assert_called_once_with()


def test_live_address_suggestions_skip_short_queries(monkeypatch):
    request = MagicMock()
    monkeypatch.setattr(data_enricher.requests, "get", request)

    assert data_enricher.get_address_suggestions("M") == []
    request.assert_not_called()


def test_resolve_address_suggestions_classifies_short_query_as_malformed():
    outcome = data_enricher.resolve_address_suggestions(
        "M", limit=10, plz_ranges=[[5000, 5999]]
    )

    assert outcome.suggestions == ()
    assert outcome.source == "live"
    assert outcome.live_status == "malformed"


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


_ADDRESS_OUTCOME_SUGGESTION = {
    "label": "Bahnhofstrasse 1",
    "lat": 47.4,
    "lon": 8.5,
    "plz": "8001",
    "upstream_only": "drop me",
}
_ADDRESS_OUTCOME_PAYLOAD = {
    "suggestions": [
        {"label": "Bahnhofstrasse 1", "lat": 47.4, "lon": 8.5, "plz": "8001"}
    ]
}


@pytest.mark.parametrize(("route", "path"), _ADDRESS_ROUTES)
def test_address_routes_consume_shared_domain_outcome(
    monkeypatch, full_app_module, client, route, path
):
    outcome = data_enricher.AddressSuggestionOutcome(
        suggestions=[dict(_ADDRESS_OUTCOME_SUGGESTION)],
        source="live",
        live_status="success",
    )
    adapter = MagicMock(return_value=outcome)
    monkeypatch.setattr(data_enricher, "resolve_address_suggestions", adapter)
    route_client = full_app_module.web.test_client() if route == "website" else client

    response = route_client.get(path, query_string={"q": "Mellingen"})

    assert response.status_code == 200
    assert response.get_json() == _ADDRESS_OUTCOME_PAYLOAD
    adapter.assert_called_once()


_MOCK_FALLBACK_QUERY = "Mellingen"
_MOCK_FALLBACK_PAYLOAD = {
    "suggestions": [{"label": "Mellingen", "lat": None, "lon": None, "plz": None}]
}


@pytest.mark.parametrize(("route", "path"), _ADDRESS_ROUTES)
def test_address_routes_fall_back_to_mock_suggestion_on_upstream_outage(
    monkeypatch,
    full_app_module,
    client,
    route,
    path,
):
    adapter = MagicMock(return_value=None)
    monkeypatch.setattr(data_enricher, "get_address_suggestions", adapter)
    route_client = full_app_module.web.test_client() if route == "website" else client

    response = route_client.get(path, query_string={"q": _MOCK_FALLBACK_QUERY})

    assert response.status_code == 200
    assert response.get_json() == _MOCK_FALLBACK_PAYLOAD
    assert adapter.call_count == 1


def test_resolve_address_suggestions_reports_malformed_upstream_payload(monkeypatch):
    adapter = MagicMock(
        return_value=[
            {"label": 1, "lat": 47.4, "lon": 8.5, "plz": "5507"},
            {"label": "   ", "lat": 47.4, "lon": 8.5, "plz": "5507"},
            {"label": "<br>", "lat": 47.4, "lon": 8.5, "plz": "5507"},
        ]
    )
    monkeypatch.setattr(data_enricher, "get_address_suggestions", adapter)

    outcome = data_enricher.resolve_address_suggestions(
        "Mellingen", limit=10, plz_ranges=[[5000, 5999]]
    )

    assert outcome.suggestions == ()
    assert outcome.source == "live"
    assert outcome.live_status == "malformed"


@pytest.mark.parametrize(
    ("live_result", "expected_suggestions", "expected_source", "expected_status"),
    (
        ([], (), "live", "no_match"),
        (
            [{"label": "<b>Mellingen</b>", "lat": 47.4, "lon": 8.3, "plz": 5507}],
            ({"label": "Mellingen", "lat": 47.4, "lon": 8.3, "plz": 5507},),
            "live",
            "success",
        ),
        (
            None,
            ({"label": "Mellingen", "lat": None, "lon": None, "plz": None},),
            "mock",
            "upstream_failure",
        ),
    ),
)
def test_resolve_address_suggestions_keeps_source_states_distinct(
    monkeypatch,
    live_result,
    expected_suggestions,
    expected_source,
    expected_status,
):
    monkeypatch.setattr(
        data_enricher, "get_address_suggestions", MagicMock(return_value=live_result)
    )

    outcome = data_enricher.resolve_address_suggestions(
        "Mellingen", limit=10, plz_ranges=[[5000, 5999]]
    )

    assert outcome.suggestions == expected_suggestions
    assert outcome.source == expected_source
    assert outcome.live_status == expected_status


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


def test_public_address_docs_omit_obsolete_upstream_failure(client):
    response = client.get("/api/v1/docs")

    assert response.status_code == 200
    assert (
        '"503": { description: "Adressvorschläge sind derzeit nicht verfügbar." }'
        not in response.get_data(as_text=True)
    )


_PROFILE_SEAM_ADDRESS = "Mellingerstrasse 12"
_PROFILE_SEAM_ESTIMATES = {
    "building_id": "seamprof01",
    "address": _PROFILE_SEAM_ADDRESS,
    "plz": 5400,
    "lat": 47.5,
    "lon": 8.3,
    "building_type": "EFH",
    "annual_consumption_kwh": 5200.0,
    "potential_pv_kwp": 14.0,
}
_PROFILE_SEAM_ROUTES = (
    ("website", "/api/check_potential"),
    ("public", "/api/v1/address/profile"),
)


@pytest.mark.parametrize(("route", "path"), _PROFILE_SEAM_ROUTES)
def test_profile_routes_consume_shared_domain_outcome(
    monkeypatch, full_app_module, client, route, path
):
    outcome = data_enricher.AddressProfileOutcome(
        estimates=dict(_PROFILE_SEAM_ESTIMATES),
        profiles=(),
        source="mock",
        live_status="upstream_failure",
    )
    adapter = MagicMock(return_value=outcome)
    monkeypatch.setattr(data_enricher, "resolve_address_profile", adapter)
    monkeypatch.setattr(
        full_app_module, "find_provisional_matches", MagicMock(return_value=[])
    )
    route_client = full_app_module.web.test_client() if route == "website" else client

    if route == "website":
        response = route_client.post(path, json={"address": _PROFILE_SEAM_ADDRESS})
    else:
        response = route_client.get(
            path, query_string={"address": _PROFILE_SEAM_ADDRESS}
        )

    assert response.status_code == 200
    if route == "website":
        body = response.get_json()
        assert body["potential"] is False
        assert body["message"] == "Keine direkten Partner gefunden."
        assert body["profile_summary"] == _PROFILE_SEAM_ESTIMATES
    else:
        assert response.get_json() == _PROFILE_SEAM_ESTIMATES
    adapter.assert_called_once_with(_PROFILE_SEAM_ADDRESS)


@pytest.mark.parametrize(
    ("live_result", "live_error", "expected_source", "expected_status"),
    (
        ((_PROFILE_SEAM_ESTIMATES, ("live-profile",)), None, "live", "success"),
        ((None, None), None, "mock", "no_match"),
        (None, RuntimeError("upstream down"), "mock", "upstream_failure"),
    ),
)
def test_resolve_address_profile_preserves_fallback_cause(
    monkeypatch,
    live_result,
    live_error,
    expected_source,
    expected_status,
):
    live = MagicMock(return_value=live_result, side_effect=live_error)
    mock = MagicMock(return_value=(_PROFILE_SEAM_ESTIMATES, ("mock-profile",)))
    monkeypatch.setattr(data_enricher, "get_energy_profile_for_address", live)
    monkeypatch.setattr(data_enricher, "get_mock_energy_profile_for_address", mock)

    outcome = data_enricher.resolve_address_profile(_PROFILE_SEAM_ADDRESS)

    assert outcome.estimates == _PROFILE_SEAM_ESTIMATES
    assert outcome.source == expected_source
    assert outcome.live_status == expected_status
    assert outcome.profiles == (
        ("mock-profile",) if expected_source == "mock" else ("live-profile",)
    )
    live.assert_called_once_with(_PROFILE_SEAM_ADDRESS)
    assert mock.call_count == int(expected_source == "mock")
    if expected_source == "mock":
        mock.assert_called_once_with(_PROFILE_SEAM_ADDRESS)
