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


def _suggest_response(results):
    response = MagicMock()
    response.json.return_value = {"results": results}
    return response


def test_live_address_suggestions_take_their_default_limit_from_the_call_site(
    monkeypatch,
):
    request = MagicMock(return_value=_suggest_response([]))
    monkeypatch.setattr(data_enricher.requests, "get", request)

    assert data_enricher.get_address_suggestions("Mellingen") == []

    _, kwargs = request.call_args
    assert kwargs["params"]["limit"] == 30, "10 requested suggestions * 3 fetch"


def test_live_address_suggestions_accept_a_two_character_query(monkeypatch):
    request = MagicMock(return_value=_suggest_response([]))
    monkeypatch.setattr(data_enricher.requests, "get", request)

    assert data_enricher.get_address_suggestions("Me") == []
    request.assert_called_once()


def test_live_address_suggestions_return_empty_for_a_payload_without_results(
    monkeypatch,
):
    response = MagicMock()
    response.json.return_value = {}
    monkeypatch.setattr(data_enricher.requests, "get", MagicMock(return_value=response))

    assert data_enricher.get_address_suggestions("Mellingen") == []


def test_live_address_suggestions_drop_results_without_attrs(monkeypatch):
    response = _suggest_response([{"no_attrs": True}])
    monkeypatch.setattr(data_enricher.requests, "get", MagicMock(return_value=response))

    assert data_enricher.get_address_suggestions("Mellingen") == []


def test_live_address_suggestions_drop_labelless_results_inside_the_range(
    monkeypatch,
):
    response = _suggest_response([{"attrs": {"plz": 5507}}])
    monkeypatch.setattr(data_enricher.requests, "get", MagicMock(return_value=response))

    assert (
        data_enricher.get_address_suggestions("Mellingen", plz_ranges=[[5000, 5999]])
        == []
    )


def test_live_address_suggestions_keep_an_integer_plz_even_when_the_label_has_no_digits(
    monkeypatch,
):
    response = _suggest_response(
        [{"attrs": {"label": "Bahnhofstrasse 1", "plz": 5507, "lat": 47.4, "lon": 8.3}}]
    )
    monkeypatch.setattr(data_enricher.requests, "get", MagicMock(return_value=response))

    assert data_enricher.get_address_suggestions(
        "Mellingen", plz_ranges=[[5000, 5999]]
    ) == [{"label": "Bahnhofstrasse 1", "lat": 47.4, "lon": 8.3, "plz": 5507}]


def test_live_address_suggestions_read_the_plz_from_the_label_without_an_attrs_plz(
    monkeypatch,
):
    response = _suggest_response(
        [{"attrs": {"label": "Dorfstrasse 2, 5507 Mellingen", "lat": 47.5, "lon": 8.4}}]
    )
    monkeypatch.setattr(data_enricher.requests, "get", MagicMock(return_value=response))

    assert data_enricher.get_address_suggestions(
        "Mellingen", plz_ranges=[[5000, 5999]]
    ) == [
        {"label": "Dorfstrasse 2, 5507 Mellingen", "lat": 47.5, "lon": 8.4, "plz": 5507}
    ]


def test_live_address_suggestions_default_to_the_zurich_plz_window(monkeypatch):
    request = MagicMock(
        return_value=_suggest_response(
            [
                {"attrs": {"label": "A, 7999 X", "plz": 7999, "lat": 1, "lon": 2}},
                {
                    "attrs": {
                        "label": "B, 8000 Zuerich",
                        "plz": 8000,
                        "lat": 3,
                        "lon": 4,
                    }
                },
                {
                    "attrs": {
                        "label": "C, 8105 Zuerich",
                        "plz": 8105,
                        "lat": 5,
                        "lon": 6,
                    }
                },
                {
                    "attrs": {
                        "label": "D, 8999 Zuerich",
                        "plz": 8999,
                        "lat": 7,
                        "lon": 8,
                    }
                },
                {
                    "attrs": {
                        "label": "E, 9000 Rapperswil",
                        "plz": 9000,
                        "lat": 9,
                        "lon": 10,
                    }
                },
            ]
        )
    )
    monkeypatch.setattr(data_enricher.requests, "get", request)

    suggestions = data_enricher.get_address_suggestions("Zuerich")

    assert [row["plz"] for row in suggestions] == [8000, 8105, 8999]


def test_live_address_suggestions_keep_searching_after_an_excluded_result(monkeypatch):
    response = _suggest_response(
        [
            {"attrs": {"label": "Outside 4000", "plz": 4000, "lat": 1, "lon": 2}},
            {"attrs": {"label": "Inside 5507", "plz": 5507, "lat": 3, "lon": 4}},
        ]
    )
    monkeypatch.setattr(data_enricher.requests, "get", MagicMock(return_value=response))

    suggestions = data_enricher.get_address_suggestions(
        "Mellingen", plz_ranges=[[5000, 5999]]
    )

    assert [row["plz"] for row in suggestions] == [5507]


def test_live_address_suggestions_keep_searching_after_label_fallback_exclusions(
    monkeypatch,
):
    response = _suggest_response(
        [
            # attrs plz is not numeric, label has no standalone four digit number
            {
                "attrs": {
                    "label": "Bahnhofstrasse 1",
                    "plz": "invalid",
                    "lat": 1,
                    "lon": 2,
                }
            },
            # no attrs plz, label has no standalone four digit number
            {"attrs": {"label": "Dorfstrasse 2", "lat": 3, "lon": 4}},
            # no attrs plz, label has a four digit number outside the range
            {"attrs": {"label": "Weg 9999 X", "lat": 5, "lon": 6}},
            # attrs plz not numeric, label has a four digit number outside the range
            {"attrs": {"label": "Aare 9999 Y", "plz": "invalid", "lat": 7, "lon": 8}},
            {"attrs": {"label": "Inside 5507", "plz": 5507, "lat": 8, "lon": 9}},
        ]
    )
    monkeypatch.setattr(data_enricher.requests, "get", MagicMock(return_value=response))

    suggestions = data_enricher.get_address_suggestions(
        "Mellingen", plz_ranges=[[5000, 5999]]
    )

    assert [row["plz"] for row in suggestions] == [5507]


def test_live_address_suggestions_report_the_upstream_failure_to_the_operator(
    monkeypatch, capsys
):
    def fail(*_args, **_kwargs):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(data_enricher.requests, "get", fail)

    assert data_enricher.get_address_suggestions("Mellingen") is None

    operator_output = capsys.readouterr().out
    assert "GEO FEHLER" in operator_output
    assert "upstream down" in operator_output


def test_live_address_suggestions_stop_at_the_requested_limit(monkeypatch):
    response = _suggest_response(
        [
            {"attrs": {"label": "A, 5507", "plz": 5507, "lat": 1, "lon": 2}},
            {"attrs": {"label": "B, 5508", "plz": 5508, "lat": 3, "lon": 4}},
        ]
    )
    monkeypatch.setattr(data_enricher.requests, "get", MagicMock(return_value=response))

    suggestions = data_enricher.get_address_suggestions(
        "Mellingen", limit=1, plz_ranges=[[5000, 5999]]
    )

    assert [row["plz"] for row in suggestions] == [5507]


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


# ==== Mock fallback seams (#507) ====


@pytest.mark.parametrize(
    ("plz", "expected"),
    (
        (5430, ("EFH", 1, 160)),
        (5432, ("EFH", 1, 160)),
        (5400, ("MFH", 8, 700)),
        (5496, ("EFH", 1, 150)),
    ),
)
def test_mock_gwr_data_returns_the_expected_building_record(plz, expected):
    assert data_enricher.mock_get_gwr_data(47.4, 8.3, plz) == expected


@pytest.mark.parametrize(
    ("plz", "expected"),
    (
        (5400, (15.0, 1.2)),
        (5430, (10.0, 1.0)),
        (5600, (5.0, 0.9)),
    ),
)
def test_mock_plz_stats_returns_the_expected_density_and_income(plz, expected):
    assert data_enricher.mock_get_plz_stats(plz) == expected


def test_mock_energy_profile_is_reproducible_for_the_same_address():
    estimates, profiles = data_enricher.get_mock_energy_profile_for_address(
        "Mellingerstrasse 12"
    )
    again, profiles_again = data_enricher.get_mock_energy_profile_for_address(
        "Mellingerstrasse 12"
    )

    assert estimates == again
    assert profiles.equals(profiles_again)


def test_mock_energy_profile_fields_for_a_known_address():
    estimates, profiles = data_enricher.get_mock_energy_profile_for_address(
        "Mellingerstrasse 12"
    )

    assert estimates["building_id"] == "da338d11ec"
    assert estimates["address"] == "Mellingerstrasse 12"
    assert estimates["plz"] in {5400, 5430, 5432}
    assert estimates["building_type"] in {"EFH", "MFH"}
    assert type(estimates["annual_consumption_kwh"]) is float
    assert type(estimates["potential_pv_kwp"]) is float
    assert type(estimates["lat"]) is float
    assert type(estimates["lon"]) is float
    assert 47.4 < estimates["lat"] < 47.55
    assert 8.2 < estimates["lon"] < 8.4
    assert list(profiles.columns) == ["consumption_kw", "production_kw"]
    assert len(profiles) == 35040


def test_mock_energy_profile_builds_profiles_from_its_own_estimates(monkeypatch):
    captured = {}
    marker = ("generated", "profiles")

    def fake_profiles(annual_consumption_kwh, potential_pv_kwp):
        captured["annual"] = annual_consumption_kwh
        captured["pv"] = potential_pv_kwp
        return marker

    monkeypatch.setattr(
        data_enricher.ml_models, "generate_mock_profiles", fake_profiles
    )

    estimates, profiles = data_enricher.get_mock_energy_profile_for_address("Testweg 5")

    assert captured["annual"] == estimates["annual_consumption_kwh"]
    assert captured["pv"] == estimates["potential_pv_kwp"]
    assert profiles is marker


_SEEDED_DRAWS = {
    "Mellingerstrasse 12": {
        "plz": 5400,
        "building_type": "MFH",
        "annual_consumption_kwh": 5354.0,
        "potential_pv_kwp": 2.0,
        "lat": 47.47410501631341,
        "lon": 8.306297674955108,
    },
    "Testweg 5": {
        "plz": 5432,
        "building_type": "EFH",
        "annual_consumption_kwh": 9014.0,
        "potential_pv_kwp": 29.0,
        "lat": 47.47300554277356,
        "lon": 8.306552279653749,
    },
    "Bahnhofstrasse 1": {
        "plz": 5430,
        "building_type": "EFH",
        "annual_consumption_kwh": 4381.0,
        "potential_pv_kwp": 4.0,
        "lat": 47.473639743605666,
        "lon": 8.30035763270587,
    },
    # Discriminating reference addresses: their seeded draws sit where a
    # shifted randint bound changes the outcome (18989 for the annual upper
    # bound, 51 for the PV upper bound).
    "W" * 18989: {
        "plz": 5400,
        "building_type": "MFH",
        "annual_consumption_kwh": 19727.0,
        "potential_pv_kwp": 28.0,
        "lat": 47.48001405942026,
        "lon": 8.305528854859302,
    },
    "X" * 51: {
        "plz": 5430,
        "building_type": "MFH",
        "annual_consumption_kwh": 16181.0,
        "potential_pv_kwp": 20.0,
        "lat": 47.471547484133254,
        "lon": 8.308560640235312,
    },
}


@pytest.mark.parametrize("address", sorted(_SEEDED_DRAWS))
def test_mock_energy_profile_pins_the_seeded_draws_for_reference_addresses(address):
    estimates, _profiles = data_enricher.get_mock_energy_profile_for_address(address)

    for field, expected in _SEEDED_DRAWS[address].items():
        assert estimates[field] == expected, f"{field} for {address!r}"


def test_mock_energy_profile_announces_the_mock_run_to_the_operator(capsys):
    data_enricher.get_mock_energy_profile_for_address("Testweg 5")

    operator_output = capsys.readouterr().out
    assert "Starte MOCK-Analyse" in operator_output
    assert "Analyse abgeschlossen" in operator_output
    assert "Testweg 5" in operator_output


# ==== Real geocoding and energy seams (#508) ====


def test_consumption_estimate_per_building_type():
    assert data_enricher.estimate_consumption_kwh("EFH", 9, 200, (15.0, 1.2)) == 5400.0
    assert data_enricher.estimate_consumption_kwh("MFH", 8, 200, (15.0, 1.2)) == 24000.0
    assert (
        data_enricher.estimate_consumption_kwh("Gewerbe", 9, 200, (15.0, 0.9)) == 3600.0
    )


def test_consumption_estimate_scales_with_the_purchasing_power_index():
    low = data_enricher.estimate_consumption_kwh("EFH", 9, 200, (15.0, 0.9))
    high = data_enricher.estimate_consumption_kwh("EFH", 9, 200, (15.0, 1.2))

    assert low == 4500 * 0.9
    assert high == 4500 * 1.2


def test_ev_estimate_charges_when_the_draw_is_below_the_probability(monkeypatch):
    monkeypatch.setattr(data_enricher.np.random, "rand", MagicMock(return_value=0.09))

    assert data_enricher.estimate_ev_kwh("EFH", (10.0, 1.0)) == 2500.0


def test_ev_estimate_charges_never_outside_single_family_homes(monkeypatch):
    monkeypatch.setattr(data_enricher.np.random, "rand", MagicMock(return_value=0.0))

    assert data_enricher.estimate_ev_kwh("MFH", (15.0, 1.2)) == 0.0
    assert data_enricher.estimate_ev_kwh("Gewerbe", (15.0, 1.2)) == 0.0


def test_ev_estimate_skips_the_charge_when_the_draw_matches_the_probability(
    monkeypatch,
):
    monkeypatch.setattr(data_enricher.np.random, "rand", MagicMock(return_value=0.1))

    assert data_enricher.estimate_ev_kwh("EFH", (10.0, 1.0)) == 0.0


def test_ev_estimate_announces_its_chance_in_percent(monkeypatch, capsys):
    monkeypatch.setattr(data_enricher.np.random, "rand", MagicMock(return_value=0.09))

    assert data_enricher.estimate_ev_kwh("EFH", (10.0, 1.0)) == 2500.0

    operator_output = capsys.readouterr().out
    assert "EV-Bedarf: Ja" in operator_output
    assert "Chance: 10.0%" in operator_output


def test_ev_estimate_applies_the_income_index_to_the_chance(monkeypatch, capsys):
    monkeypatch.setattr(data_enricher.np.random, "rand", MagicMock(return_value=0.05))

    assert data_enricher.estimate_ev_kwh("EFH", (10.0, 0.8)) == 2500.0

    assert "Chance: 8.0%" in capsys.readouterr().out


def test_ev_estimate_announces_the_missed_charge_with_its_chance(monkeypatch, capsys):
    monkeypatch.setattr(data_enricher.np.random, "rand", MagicMock(return_value=0.5))

    assert data_enricher.estimate_ev_kwh("EFH", (10.0, 1.0)) == 0.0

    operator_output = capsys.readouterr().out
    assert "EV-Bedarf: Nein" in operator_output
    assert "Chance: 10.0%" in operator_output


def _geo_response(payload):
    response = MagicMock()
    response.json.return_value = {
        "results": [
            {
                "attrs": {
                    "lat": 47.5,
                    "lon": 8.3,
                    "plz": 5400,
                    "label": "Bahnhofstrasse 1, 5400 Baden",
                }
            }
        ]
    }
    return response


def test_coordinates_pin_the_geoadmin_query(monkeypatch):
    request = MagicMock(return_value=_geo_response({}))
    monkeypatch.setattr(data_enricher.requests, "get", request)

    assert data_enricher.get_coordinates_from_address("Bahnhofstrasse 1 Baden") == (
        47.5,
        8.3,
        5400,
    )

    args, kwargs = request.call_args
    assert args[0] == data_enricher.GEO_API_URL
    assert kwargs["params"] == {
        "searchText": "Bahnhofstrasse 1 Baden",
        "type": "locations",
        "limit": 1,
    }


def test_coordinates_read_the_plz_from_the_label_without_an_attrs_plz(monkeypatch):
    response = MagicMock()
    response.json.return_value = {
        "results": [
            {
                "attrs": {
                    "lat": 47.5,
                    "lon": 8.3,
                    "label": "Bahnhofstrasse 1, 5400 Baden",
                }
            }
        ]
    }
    monkeypatch.setattr(data_enricher.requests, "get", MagicMock(return_value=response))

    assert data_enricher.get_coordinates_from_address("Bahnhofstrasse 1 Baden") == (
        47.5,
        8.3,
        5400,
    )


def test_coordinates_stay_none_without_any_plz(monkeypatch):
    response = MagicMock()
    response.json.return_value = {
        "results": [{"attrs": {"lat": 47.5, "lon": 8.3, "label": "Bahnhofstrasse 1"}}]
    }
    monkeypatch.setattr(data_enricher.requests, "get", MagicMock(return_value=response))

    assert data_enricher.get_coordinates_from_address("Bahnhofstrasse 1 Baden") == (
        47.5,
        8.3,
        None,
    )


def test_coordinates_report_nothing_when_the_search_finds_nothing(monkeypatch):
    response = MagicMock()
    response.json.return_value = {"results": []}
    monkeypatch.setattr(data_enricher.requests, "get", MagicMock(return_value=response))

    assert data_enricher.get_coordinates_from_address("Nirgendwo 1") == (
        None,
        None,
        None,
    )


def test_coordinates_report_nothing_when_the_upstream_fails(monkeypatch, capsys):
    def fail(*_args, **_kwargs):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(data_enricher.requests, "get", fail)

    assert data_enricher.get_coordinates_from_address("Bahnhofstrasse 1 Baden") == (
        None,
        None,
        None,
    )

    operator_output = capsys.readouterr().out
    assert "GEO FEHLER" in operator_output
    assert "upstream down" in operator_output


def test_pv_potential_queries_the_sonnendach_layer(monkeypatch):
    request = MagicMock()
    response = MagicMock()
    response.json.return_value = {"results": [{"attributes": {"strom_a": 9500}}]}
    request.return_value = response
    monkeypatch.setattr(data_enricher.requests, "get", request)

    assert data_enricher.get_pv_potential_from_coords(47.5, 8.3) == (9500, 9.5)

    args, kwargs = request.call_args
    assert args[0] == f"{data_enricher.SOLAR_API_URL}/identify"
    map_extent = [float(part) for part in kwargs["params"]["mapExtent"].split(",")]
    assert map_extent == pytest.approx([-1.7, 37.5, 18.3, 57.5])
    assert kwargs["params"] == {
        "geometry": "8.3,47.5",
        "geometryType": "esriGeometryPoint",
        "imageDisplay": "1,1,1",
        "tolerance": 2,
        "returnGeometry": "false",
        "layers": "all:ch.bfe.sonnendach",
        "mapExtent": kwargs["params"]["mapExtent"],
    }


def test_pv_potential_reports_zero_without_a_building(monkeypatch, capsys):
    response = MagicMock()
    response.json.return_value = {"results": []}
    monkeypatch.setattr(data_enricher.requests, "get", MagicMock(return_value=response))

    assert data_enricher.get_pv_potential_from_coords(47.5, 8.3) == (0, 0)
    assert "Kein Gebäude" in capsys.readouterr().out


def test_pv_potential_reports_zero_without_a_production_value(monkeypatch):
    response = MagicMock()
    response.json.return_value = {"results": [{"attributes": {}}]}
    monkeypatch.setattr(data_enricher.requests, "get", MagicMock(return_value=response))

    assert data_enricher.get_pv_potential_from_coords(47.5, 8.3) == (0, 0)


def test_pv_potential_reports_zero_when_the_upstream_fails(monkeypatch, capsys):
    def fail(*_args, **_kwargs):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(data_enricher.requests, "get", fail)

    assert data_enricher.get_pv_potential_from_coords(47.5, 8.3) == (0, 0)
    assert "GEO FEHLER" in capsys.readouterr().out


def test_profile_walks_coordinates_pv_statistics_and_profiles(monkeypatch, capsys):
    captured_calls = []
    captured_profiles = {}
    marker = ("generated", "profiles")

    def fake_get(url, params=None):
        captured_calls.append(url)
        if url == data_enricher.GEO_API_URL:
            return _geo_response({})
        response = MagicMock()
        response.json.return_value = {"results": [{"attributes": {"strom_a": 9500}}]}
        return response

    def fake_profiles(annual_consumption_kwh, potential_pv_kwp):
        captured_profiles["annual"] = annual_consumption_kwh
        captured_profiles["pv"] = potential_pv_kwp
        return marker

    monkeypatch.setattr(data_enricher.requests, "get", fake_get)
    monkeypatch.setattr(
        data_enricher.ml_models, "generate_mock_profiles", fake_profiles
    )
    monkeypatch.setattr(data_enricher.np.random, "rand", MagicMock(return_value=0.0))

    estimates, profiles = data_enricher.get_energy_profile_for_address(
        "Mellingerstrasse 12, <b>5400</b> Baden"
    )

    assert captured_calls == [
        data_enricher.GEO_API_URL,
        f"{data_enricher.SOLAR_API_URL}/identify",
    ]
    assert estimates == {
        "building_id": "a37de1c8ca",
        "address": "Mellingerstrasse 12, 5400 Baden",
        "plz": 5400,
        "lat": 47.5,
        "lon": 8.3,
        "building_type": "MFH",
        "annual_consumption_kwh": 24000.0,
        "potential_pv_kwp": 9.5,
    }
    assert captured_profiles["annual"] == 24000.0
    assert captured_profiles["pv"] == 9.5
    assert profiles is marker
    flow_output = capsys.readouterr().out
    assert "Starte ECHTE Analyse" in flow_output
    assert "Analyse abgeschlossen" in flow_output
    assert "Mellingerstrasse 12, 5400 Baden" in flow_output


def test_profile_walks_a_single_family_home_with_an_ev_charge(monkeypatch):
    response = MagicMock()
    response.json.return_value = {
        "results": [
            {
                "attrs": {
                    "lat": 47.5,
                    "lon": 8.4,
                    "plz": 5430,
                    "label": "Dorfstrasse 2, 5430 Mellingen",
                }
            }
        ]
    }

    def fake_get(url, params=None):
        if url == data_enricher.GEO_API_URL:
            return response
        solar = MagicMock()
        solar.json.return_value = {"results": [{"attributes": {"strom_a": 7800}}]}
        return solar

    marker = ("generated", "profiles")
    captured = {}
    monkeypatch.setattr(data_enricher.requests, "get", fake_get)
    monkeypatch.setattr(
        data_enricher.ml_models,
        "generate_mock_profiles",
        lambda annual_consumption_kwh, potential_pv_kwp: (
            captured.update(annual=annual_consumption_kwh, pv=potential_pv_kwp)
            or marker
        ),
    )
    monkeypatch.setattr(data_enricher.np.random, "rand", MagicMock(return_value=0.05))

    estimates, profiles = data_enricher.get_energy_profile_for_address(
        "Dorfstrasse 2, 5430 Mellingen"
    )

    assert estimates["building_type"] == "EFH"
    assert estimates["annual_consumption_kwh"] == 7000.0
    assert estimates["potential_pv_kwp"] == 7.8
    assert captured["annual"] == 7000.0
    assert captured["pv"] == 7.8
    assert profiles is marker


def test_profile_reports_nothing_without_coordinates(monkeypatch, capsys):
    response = MagicMock()
    response.json.return_value = {"results": []}
    monkeypatch.setattr(data_enricher.requests, "get", MagicMock(return_value=response))
    profiles_builder = MagicMock()
    monkeypatch.setattr(
        data_enricher.ml_models, "generate_mock_profiles", profiles_builder
    )

    assert data_enricher.get_energy_profile_for_address("Nirgendwo 1") == (None, None)
    assert data_enricher.requests.get.call_count == 1
    profiles_builder.assert_not_called()
    assert "Keine Koordinaten gefunden" in capsys.readouterr().out


def test_profile_swallows_an_unclosed_markup_opening(monkeypatch):
    response = MagicMock()
    response.json.return_value = {"results": []}
    monkeypatch.setattr(data_enricher.requests, "get", MagicMock(return_value=response))
    captured = {}

    def fake_get(url, params=None):
        captured["search"] = params["searchText"]
        return response

    monkeypatch.setattr(data_enricher.requests, "get", fake_get)

    data_enricher.get_energy_profile_for_address("Weg ohne Ende <x rest")

    assert captured["search"] == "Weg ohne Ende"


def test_coordinates_announce_the_search_to_the_operator(monkeypatch, capsys):
    monkeypatch.setattr(
        data_enricher.requests, "get", MagicMock(return_value=_geo_response({}))
    )

    data_enricher.get_coordinates_from_address("Bahnhofstrasse 1 Baden")

    operator_output = capsys.readouterr().out
    assert "Suche Koordinaten" in operator_output
    assert "Bahnhofstrasse 1 Baden" in operator_output


def test_coordinates_miss_is_announced_when_the_response_has_no_results(
    monkeypatch, capsys
):
    response = MagicMock()
    response.json.return_value = {}
    monkeypatch.setattr(data_enricher.requests, "get", MagicMock(return_value=response))

    assert data_enricher.get_coordinates_from_address("Nirgendwo 1") == (
        None,
        None,
        None,
    )

    assert "Adresse nicht gefunden." in capsys.readouterr().out


def test_coordinates_use_the_attrs_plz_without_label_fallback(monkeypatch):
    response = MagicMock()
    response.json.return_value = {
        "results": [
            {
                "attrs": {
                    "lat": 47.5,
                    "lon": 8.3,
                    "plz": 5400,
                    "label": "Bahnhofstrasse 1",
                }
            }
        ]
    }
    monkeypatch.setattr(data_enricher.requests, "get", MagicMock(return_value=response))

    assert data_enricher.get_coordinates_from_address("Bahnhofstrasse 1 Baden") == (
        47.5,
        8.3,
        5400,
    )


def test_coordinates_survive_attrs_without_label_and_plz(monkeypatch):
    response = MagicMock()
    response.json.return_value = {"results": [{"attrs": {"lat": 47.5, "lon": 8.3}}]}
    monkeypatch.setattr(data_enricher.requests, "get", MagicMock(return_value=response))

    assert data_enricher.get_coordinates_from_address("Bahnhofstrasse 1 Baden") == (
        47.5,
        8.3,
        None,
    )


def test_pv_potential_announces_the_search(monkeypatch, capsys):
    response = MagicMock()
    response.json.return_value = {"results": [{"attributes": {"strom_a": 9500}}]}
    monkeypatch.setattr(data_enricher.requests, "get", MagicMock(return_value=response))

    data_enricher.get_pv_potential_from_coords(47.5, 8.3)

    operator_output = capsys.readouterr().out
    assert "Suche PV-Potenzial bei (47.5, 8.3)" in operator_output
    assert "GEO OK" in operator_output


def test_pv_potential_announces_a_clean_zero_without_a_production_value(
    monkeypatch, capsys
):
    response = MagicMock()
    response.json.return_value = {"results": [{"attributes": {}}]}
    monkeypatch.setattr(data_enricher.requests, "get", MagicMock(return_value=response))

    assert data_enricher.get_pv_potential_from_coords(47.5, 8.3) == (0, 0)

    operator_output = capsys.readouterr().out
    assert "GEO OK" in operator_output
    assert "Produktion: 0" in operator_output
