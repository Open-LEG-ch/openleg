# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for public_data.py: fetchers and computed metrics."""

import csv
import logging
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest

import public_data

_ER_HEADER_WITHOUT_KANTON = (
    "BFS_NR;GEMEINDENAME;anteil_dachflaechen_solar;anteil_ev;"
    "anteil_erneuerbar_heizen;stromverbrauch_mwh;erneuerbare_produktion_mwh"
)
_ER_HEADER = f"{_ER_HEADER_WITHOUT_KANTON};KANTON\n"
_SD_HEADER = (
    "BFS_NR;dachflaeche_total_m2;dachflaeche_geeignet_m2;"
    "potenzial_kwh_jahr;potenzial_kwp;auslastung_pct\n"
)
_SD_DATA_COLUMNS = (
    "dachflaeche_total_m2;dachflaeche_geeignet_m2;"
    "potenzial_kwh_jahr;potenzial_kwp;auslastung_pct"
)


def _metadata_response():
    response = MagicMock()
    response.json.return_value = {
        "result": {
            "resources": [
                {
                    "format": "CSV",
                    "name": "Gemeindedaten",
                    "url": "https://data.example/municipalities.csv",
                }
            ]
        }
    }
    return response


def _csv_response(text):
    response = MagicMock(text=text, apparent_encoding="utf-8")
    return response


@pytest.mark.parametrize(
    ("fetcher", "header"),
    [
        (
            public_data.fetch_energie_reporter,
            _ER_HEADER,
        ),
        (public_data.fetch_sonnendach_municipal, _SD_HEADER),
    ],
)
def test_bulk_fetchers_distinguish_a_healthy_empty_dataset(
    monkeypatch, fetcher, header
):
    get = MagicMock(side_effect=[_metadata_response(), _csv_response(header)])
    monkeypatch.setattr(public_data.requests, "get", get)

    assert fetcher() == []


@pytest.mark.parametrize(
    ("fetcher", "header"),
    [
        (public_data.fetch_energie_reporter, "upstream-secret-column;OTHER\n1;2\n"),
        (public_data.fetch_energie_reporter, "BFS_NR;GEMEINDENAME\n261;Town\n"),
        (
            public_data.fetch_energie_reporter,
            f"{_ER_HEADER_WITHOUT_KANTON}\n261;Town;1;2;3;4;5\n",
        ),
        (
            public_data.fetch_sonnendach_municipal,
            "upstream-secret-column;OTHER\n1;2\n",
        ),
        (public_data.fetch_sonnendach_municipal, "BFS_NR;OTHER\n261;2\n"),
        (
            public_data.fetch_sonnendach_municipal,
            "BFS_NR;potenzial_kwp\n261;500\n",
        ),
    ],
)
def test_bulk_fetchers_reject_an_incompatible_csv_schema(
    monkeypatch, caplog, fetcher, header
):
    secret = "upstream-secret-column"
    get = MagicMock(side_effect=[_metadata_response(), _csv_response(header)])
    monkeypatch.setattr(public_data.requests, "get", get)

    with caplog.at_level(logging.WARNING):
        result = fetcher()

    assert result is None
    assert secret not in caplog.text


@pytest.mark.parametrize(
    "fetcher",
    [public_data.fetch_energie_reporter, public_data.fetch_sonnendach_municipal],
)
@pytest.mark.parametrize("failure", ["request", "metadata", "download", "parse"])
def test_bulk_fetchers_return_failure_without_leaking_details(
    monkeypatch, caplog, fetcher, failure
):
    secret = "token=upstream-secret response=private-body"
    metadata = _metadata_response()
    if failure == "request":
        get = MagicMock(side_effect=RuntimeError(secret))
    elif failure == "metadata":
        metadata.json.return_value = {"result": {"resources": []}}
        get = MagicMock(return_value=metadata)
    elif failure == "download":
        get = MagicMock(side_effect=[metadata, RuntimeError(secret)])
    else:
        get = MagicMock(side_effect=[metadata, _csv_response("broken")])
        monkeypatch.setattr(
            public_data.csv,
            "DictReader",
            MagicMock(side_effect=csv.Error(secret)),
        )
    monkeypatch.setattr(public_data.requests, "get", get)

    with caplog.at_level(logging.WARNING):
        result = fetcher()

    assert result is None
    assert secret not in caplog.text


class TestComputeValueGap:
    """Test LEG value-gap calculation."""

    def test_basic_value_gap(self):
        from public_data import compute_leg_value_gap

        h4 = {"grid_rp_kwh": 9.5, "total_rp_kwh": 27.5}
        result = compute_leg_value_gap(h4, grid_reduction_pct=40.0)
        assert result["annual_savings_chf"] > 0
        assert result["monthly_savings_chf"] > 0
        assert result["savings_pct"] > 0
        assert result["grid_fee_rp_kwh"] == 9.5
        # 9.5 * 0.4 = 3.8 Rp/kWh * 4500 kWh / 100 = 171 CHF
        assert result["annual_savings_chf"] == 171.0

    def test_zero_grid_fee(self):
        from public_data import compute_leg_value_gap

        result = compute_leg_value_gap({"grid_rp_kwh": 0, "total_rp_kwh": 27.0})
        assert result["annual_savings_chf"] == 0

    def test_empty_tariff(self):
        from public_data import compute_leg_value_gap

        result = compute_leg_value_gap({})
        assert result["annual_savings_chf"] == 0

    def test_ne5_reduction(self):
        from public_data import compute_leg_value_gap

        h4 = {"grid_rp_kwh": 9.5, "total_rp_kwh": 27.5}
        result = compute_leg_value_gap(h4, grid_reduction_pct=25.0)
        # 9.5 * 0.25 = 2.375 * 4500 / 100 = 106.875
        assert result["annual_savings_chf"] == 106.88


class TestValueGapGuards:
    def test_the_zero_gap_result_carries_the_three_zero_keys(self):
        from public_data import compute_leg_value_gap

        result = compute_leg_value_gap({"grid_rp_kwh": -1, "total_rp_kwh": 27.5})

        assert result == {
            "annual_savings_chf": 0,
            "monthly_savings_chf": 0,
            "savings_pct": 0,
        }

    def test_a_tariff_without_a_total_is_zero_gap(self):
        from public_data import compute_leg_value_gap

        assert compute_leg_value_gap({"grid_rp_kwh": 9.5}) == {
            "annual_savings_chf": 0,
            "monthly_savings_chf": 0,
            "savings_pct": 0,
        }

    def test_a_tariff_without_a_grid_fee_is_zero_gap(self):
        from public_data import compute_leg_value_gap

        assert compute_leg_value_gap({"total_rp_kwh": 27.5}) == {
            "annual_savings_chf": 0,
            "monthly_savings_chf": 0,
            "savings_pct": 0,
        }


class TestValueGapNumbers:
    def test_the_full_breakdown_uses_the_default_forty_percent_reduction(self):
        from public_data import compute_leg_value_gap

        result = compute_leg_value_gap({"grid_rp_kwh": 9.55, "total_rp_kwh": 27.57})

        assert result == {
            "grid_fee_rp_kwh": 9.55,
            "savings_rp_kwh": 3.82,
            "annual_savings_chf": 171.9,
            "monthly_savings_chf": 14.33,
            "savings_pct": 13.9,
            "grid_reduction_pct": 40.0,
            "assumed_consumption_kwh": 4500,
        }


class TestTransitionScoreGuards:
    def test_a_profile_without_consumption_keeps_no_production_ratio(self):
        from public_data import compute_energy_transition_score

        assert compute_energy_transition_score({"renewable_production_mwh": 50}) == 0.0

    def test_a_profile_without_production_keeps_no_production_ratio(self):
        from public_data import compute_energy_transition_score

        assert (
            compute_energy_transition_score({"electricity_consumption_mwh": 100}) == 0.0
        )

    def test_an_empty_consumption_value_is_not_a_consumption_of_one(self):
        from public_data import compute_energy_transition_score

        assert (
            compute_energy_transition_score(
                {"renewable_production_mwh": 10, "electricity_consumption_mwh": ""}
            )
            == 0.0
        )


class TestTransitionScoreRatioBounds:
    def test_a_doubled_production_ratio_caps_at_one(self):
        from public_data import compute_energy_transition_score

        score = compute_energy_transition_score(
            {"electricity_consumption_mwh": 100, "renewable_production_mwh": 200}
        )

        assert score == 25.0, (
            "the production ratio contributes its capped quarter at most"
        )

    def test_a_single_megawatt_hour_consumption_still_uses_the_ratio(self):
        from public_data import compute_energy_transition_score

        score = compute_energy_transition_score(
            {"electricity_consumption_mwh": 1, "renewable_production_mwh": 1}
        )

        assert score == 25.0, "consumption of one counts, not zero"


class TestValueGapMoneyBounds:
    def test_a_single_rappen_grid_fee_stills_bills_a_full_breakdown(self):
        from public_data import compute_leg_value_gap

        result = compute_leg_value_gap({"grid_rp_kwh": 1.0, "total_rp_kwh": 27.5})

        assert result == {
            "grid_fee_rp_kwh": 1.0,
            "savings_rp_kwh": 0.4,
            "annual_savings_chf": 18.0,
            "monthly_savings_chf": 1.5,
            "savings_pct": 1.5,
            "grid_reduction_pct": 40.0,
            "assumed_consumption_kwh": 4500,
        }

    def test_third_decimal_grid_fee_is_reported_to_two_places(self):
        from public_data import compute_leg_value_gap

        result = compute_leg_value_gap({"grid_rp_kwh": 9.555, "total_rp_kwh": 27.57})

        assert result["savings_rp_kwh"] == 3.82
        assert result["grid_fee_rp_kwh"] == 9.55
        assert result["annual_savings_chf"] == 171.99

    def test_a_half_rappen_total_still_shares_the_savings(self):
        from public_data import compute_leg_value_gap

        result = compute_leg_value_gap({"grid_rp_kwh": 9.5, "total_rp_kwh": 0.5})

        assert result["savings_pct"] == 760.0


class TestTransitionScoreBounds:
    @pytest.mark.parametrize(
        ("field", "value", "expected"),
        [
            ("solar_potential_pct", 101, 30.0),
            ("ev_share_pct", 31, 20.0),
            ("renewable_heating_pct", 101, 25.0),
        ],
    )
    def test_component_shares_clip_to_their_maximum(self, field, value, expected):
        from public_data import compute_energy_transition_score

        profile = {"electricity_consumption_mwh": 100, "renewable_production_mwh": 0}
        profile[field] = value

        assert compute_energy_transition_score(profile) == expected


class TestParseDecimal:
    def test_a_binding_without_a_value_counts_as_zero(self):
        from public_data import _parse_decimal

        assert _parse_decimal({"type": "typed"}) == 0.0

    def test_a_non_finite_and_unparseable_value_reads_as_none(self):
        from public_data import _parse_decimal

        assert _parse_decimal({"value": "abc"}) is None
        assert _parse_decimal(None) is None
        assert _parse_decimal({"type": "typed"}) == 0.0
        assert _parse_decimal({"value": "27.5"}) == 27.5


class TestElComQuery:
    _RESULT: ClassVar = {
        "bfs_number": 261,
        "year": 2026,
        "operator_name": "EKZ",
        "category": "H4",
        "total_rp_kwh": 27.5,
        "energy_rp_kwh": 12.0,
        "grid_rp_kwh": 9.5,
        "municipality_fee_rp_kwh": 0.6,
        "kev_rp_kwh": 0.5,
    }

    def test_the_lindas_query_and_one_tariff_record_are_pinned(self):
        from public_data import fetch_elcom_tariffs

        response = MagicMock()
        response.json.return_value = {
            "results": {
                "bindings": [
                    {
                        "operator": {"value": "EKZ"},
                        "category": {"value": "H4"},
                        "total": {"value": "27.5"},
                        "energy": {"value": "12.0"},
                        "grid": {"value": "9.5"},
                        "municipality_fee": {"value": "0.6"},
                        "kev": {"value": "0.5"},
                    }
                ]
            }
        }
        post = MagicMock(return_value=response)
        with patch("public_data.requests.post", post):
            result = fetch_elcom_tariffs(261, 2026)

        assert result == [self._RESULT]
        post.assert_called_once()
        assert post.call_args.args[0] == public_data.LINDAS_ENDPOINT
        _, kwargs = post.call_args
        assert kwargs["headers"] == {"Accept": "application/sparql-results+json"}
        assert kwargs["timeout"] == 30
        sparql = kwargs["data"]["query"]
        assert "elcom:municipality <https://ld.admin.ch/municipality/261> ;" in sparql
        assert 'elcom:period "2026"^^' in sparql
        assert "kwargs" not in sparql

    def test_the_record_year_defaults_to_the_tariff_year(self):
        from public_data import fetch_elcom_tariffs

        response = MagicMock()
        response.json.return_value = {"results": {"bindings": []}}
        post = MagicMock(return_value=response)
        with patch("public_data.requests.post", post):
            result = fetch_elcom_tariffs(261)

        assert result == []
        sparql = post.call_args.kwargs["data"]["query"]
        assert 'elcom:period "2026"^^' in sparql

    def test_missing_results_read_as_an_empty_tariff_list(self):
        from public_data import fetch_elcom_tariffs

        response = MagicMock()
        response.json.return_value = {"no_results": True}
        post = MagicMock(return_value=response)
        with patch("public_data.requests.post", post):
            assert fetch_elcom_tariffs(261, 2026) == []

    def test_bindless_records_read_the_public_separator_values(self):
        from public_data import fetch_elcom_tariffs

        response = MagicMock()
        response.json.return_value = {
            "results": {
                "bindings": [
                    {
                        "operator": {"value": "EKZ"},
                        "total": {"value": "27.5"},
                    }
                ]
            }
        }
        post = MagicMock(return_value=response)
        with patch("public_data.requests.post", post):
            result = fetch_elcom_tariffs(261, 2026)

        assert result == [
            {
                "bfs_number": 261,
                "year": 2026,
                "operator_name": "EKZ",
                "category": "",
                "total_rp_kwh": 27.5,
                "energy_rp_kwh": None,
                "grid_rp_kwh": None,
                "municipality_fee_rp_kwh": None,
                "kev_rp_kwh": None,
            }
        ]

    def test_configuring_a_binding_without_an_operator_degrades_to_values(self):
        from public_data import fetch_elcom_tariffs

        response = MagicMock()
        response.json.return_value = {
            "results": {"bindings": [{"total": {"value": "27.5"}}]}
        }
        post = MagicMock(return_value=response)
        with patch("public_data.requests.post", post):
            result = fetch_elcom_tariffs(261, 2026)

        assert result[0]["operator_name"] == ""


class TestBulkFetcherWiring:
    @pytest.mark.parametrize(
        ("fetcher", "metadata_url"),
        [
            (public_data.fetch_energie_reporter, public_data.ENERGIE_REPORTER_URL),
            (public_data.fetch_sonnendach_municipal, public_data.SONNENDACH_URL),
        ],
    )
    def test_the_metadata_and_resource_calls_are_pinned(
        self, monkeypatch, fetcher, metadata_url
    ):
        get = MagicMock(side_effect=[_metadata_response(), _csv_response(_ER_HEADER)])
        monkeypatch.setattr(public_data.requests, "get", get)

        fetcher()

        assert get.call_args_list[0].args == (metadata_url,)
        assert get.call_args_list[0].kwargs == {"timeout": 15}
        assert get.call_args_list[1].args == (
            "https://data.example/municipalities.csv",
        )
        assert get.call_args_list[1].kwargs == {"timeout": 30}


class TestBulkFetcherRecords:
    def test_the_municipal_resource_names_are_pinned_per_token(self, monkeypatch):
        """Exactly one token identifies the municipal resource: gemeinde,
        municipal or kommun — the preference must use the named one, not a
        hand-waved substring."""
        token_names = {
            "gemeinde": "Gemeindedaten der Kantone",
            "municipal": "Municipal roof data",
            "kommun": "Dachauswertung für Kommunen",
        }
        for token, name in token_names.items():
            metadata = MagicMock()
            metadata.json.return_value = {
                "result": {
                    "resources": [
                        {
                            "format": "CSV",
                            "name": "Gebäudedaten",
                            "url": "https://data.example/buildings.csv",
                        },
                        {
                            "format": "CSV",
                            "name": name,
                            "url": "https://data.example/municipalities.csv",
                        },
                    ]
                }
            }
            get = MagicMock(side_effect=[metadata, _csv_response(_SD_HEADER + "295\n")])
            monkeypatch.setattr(public_data.requests, "get", get)

            public_data.fetch_sonnendach_municipal()
            assert get.call_args_list[1].args == (
                "https://data.example/municipalities.csv",
            ), f"the {token} resource must be preferred over the first CSV"

    def test_the_sonnendach_decode_falls_back_to_utf_8(self, monkeypatch):
        csv_response = _csv_response(_SD_HEADER + "295;München\n")
        csv_response.apparent_encoding = None
        monkeypatch.setattr(
            public_data.requests,
            "get",
            MagicMock(side_effect=[_metadata_response(), csv_response]),
        )

        public_data.fetch_sonnendach_municipal()

        assert csv_response.encoding == "utf-8"

    def test_a_sonnendach_resource_without_a_format_falls_back_to_any_csv(
        self, monkeypatch, caplog
    ):
        metadata = MagicMock()
        metadata.json.return_value = {
            "result": {
                "resources": [
                    {
                        "name": "Gemeindedaten",
                        "url": "https://data.example/municipalities.csv",
                    },
                    {
                        "format": "CSV",
                        "name": "Gebäude",
                        "url": "https://data.example/buildings.csv",
                    },
                ]
            }
        }
        get = MagicMock(side_effect=[metadata, _csv_response(_SD_HEADER + "295\n")])
        monkeypatch.setattr(public_data.requests, "get", get)

        with caplog.at_level(logging.WARNING):
            assert public_data.fetch_sonnendach_municipal() == [
                {
                    "bfs_number": 295,
                    "total_roof_area_m2": None,
                    "suitable_roof_area_m2": None,
                    "potential_kwh_year": None,
                    "potential_kwp": None,
                    "utilization_pct": None,
                }
            ]

        assert "municipalities.csv" not in caplog.text

    def test_the_bulk_fetchers_log_the_exact_parsed_counts(self, monkeypatch, caplog):
        for fetcher, header, expected in (
            (
                public_data.fetch_energie_reporter,
                _ER_HEADER + "261\n",
                "[PUBLIC_DATA] Energie Reporter: 1 municipalities parsed",
            ),
            (
                public_data.fetch_sonnendach_municipal,
                _SD_HEADER + "261\n",
                "[PUBLIC_DATA] Sonnendach: 1 municipalities parsed",
            ),
        ):
            get = MagicMock(side_effect=[_metadata_response(), _csv_response(header)])
            monkeypatch.setattr(public_data.requests, "get", get)
            caplog.clear()

            with caplog.at_level(logging.INFO):
                fetcher()

            assert any(item.getMessage() == expected for item in caplog.records), (
                caplog.messages
            )

    def test_a_energie_reporter_row_without_a_name_reads_empty(self, monkeypatch):
        get = MagicMock(
            side_effect=[
                _metadata_response(),
                _csv_response(_ER_HEADER + "265\n"),
            ]
        )
        monkeypatch.setattr(public_data.requests, "get", get)

        assert public_data.fetch_energie_reporter() == [
            {
                "bfs_number": 265,
                "name": "",
                "kanton": "",
                "solar_potential_pct": None,
                "solar_installed_kwp": None,
                "ev_share_pct": None,
                "renewable_heating_pct": None,
                "electricity_consumption_mwh": None,
                "renewable_production_mwh": None,
            }
        ]

    def test_the_energie_reporter_name_aliases_are_all_read(self, monkeypatch):
        for name_column in ("GEMEINDENAME", "gemeindename", "name"):
            header = (
                f"BFS_NR;{name_column};anteil_dachflaechen_solar;anteil_ev;"
                "anteil_erneuerbar_heizen;stromverbrauch_mwh;erneuerbare_produktion_mwh;KANTON\n"
            )
            get = MagicMock(
                side_effect=[
                    _metadata_response(),
                    _csv_response(header + "295;Horgen;1;2;3;4;5;ZH\n"),
                ]
            )
            monkeypatch.setattr(public_data.requests, "get", get)

            result = public_data.fetch_energie_reporter()
            assert result is not None and result[0]["name"] == "Horgen", name_column

    def test_a_german_energie_reporter_row_yields_the_exact_record(self, monkeypatch):
        csv_response = _csv_response(
            _ER_HEADER + "261;Rüti (ZH);47.5;12.0;55.5;3200.50;2800.25;ZH\n"
        )
        get = MagicMock(side_effect=[_metadata_response(), csv_response])
        monkeypatch.setattr(public_data.requests, "get", get)

        assert public_data.fetch_energie_reporter() == [
            {
                "bfs_number": 261,
                "name": "Rüti (ZH)",
                "kanton": "ZH",
                "solar_potential_pct": 47.5,
                "solar_installed_kwp": None,
                "ev_share_pct": 12.0,
                "renewable_heating_pct": 55.5,
                "electricity_consumption_mwh": 3200.5,
                "renewable_production_mwh": 2800.25,
            }
        ]
        assert get.call_args_list[1].args == (
            "https://data.example/municipalities.csv",
        )
        assert get.call_args_list[1].kwargs == {"timeout": 30}

    def test_the_text_decoding_falls_back_to_utf_8(self, monkeypatch):
        csv_response = _csv_response(_ER_HEADER + "261;München\n")
        csv_response.apparent_encoding = None
        monkeypatch.setattr(
            public_data.requests,
            "get",
            MagicMock(side_effect=[_metadata_response(), csv_response]),
        )

        public_data.fetch_energie_reporter()

        assert csv_response.encoding == "utf-8"

    def test_a_resource_without_a_format_entry_is_not_a_csv_candidate(
        self, monkeypatch, caplog
    ):
        metadata = MagicMock()
        metadata.json.return_value = {
            "result": {
                "resources": [
                    {
                        "name": "Gemeindedaten",
                        "url": "https://data.example/municipalities.csv",
                    },
                ]
            }
        }
        get = MagicMock(side_effect=[metadata])
        monkeypatch.setattr(public_data.requests, "get", get)

        with caplog.at_level(logging.WARNING):
            assert public_data.fetch_energie_reporter() is None

        assert "no supported resource found" in caplog.text

    def test_an_english_energie_reporter_row_yields_the_same_record(self, monkeypatch):
        get = MagicMock(
            side_effect=[
                _metadata_response(),
                _csv_response(
                    "bfs_nr;name;kanton;solar_potential_pct;ev_share_pct;"
                    "renewable_heating_pct;electricity_consumption_mwh;"
                    "renewable_production_mwh\n"
                    "261;Rüti (ZH);ZH;47.5;12.0;55.5;3200.50;2800.25\n"
                ),
            ]
        )
        monkeypatch.setattr(public_data.requests, "get", get)

        assert public_data.fetch_energie_reporter() == [
            {
                "bfs_number": 261,
                "name": "Rüti (ZH)",
                "kanton": "ZH",
                "solar_potential_pct": 47.5,
                "solar_installed_kwp": None,
                "ev_share_pct": 12.0,
                "renewable_heating_pct": 55.5,
                "electricity_consumption_mwh": 3200.5,
                "renewable_production_mwh": 2800.25,
            }
        ]

    def test_missing_energie_reporter_fields_read_as_none(self, monkeypatch):
        get = MagicMock(
            side_effect=[
                _metadata_response(),
                _csv_response(_ER_HEADER + "261;Rüti (ZH)\n"),
            ]
        )
        monkeypatch.setattr(public_data.requests, "get", get)

        assert public_data.fetch_energie_reporter() == [
            {
                "bfs_number": 261,
                "name": "Rüti (ZH)",
                "kanton": "",
                "solar_potential_pct": None,
                "solar_installed_kwp": None,
                "ev_share_pct": None,
                "renewable_heating_pct": None,
                "electricity_consumption_mwh": None,
                "renewable_production_mwh": None,
            }
        ]

    def test_a_german_sonnendach_row_yields_the_exact_record(self, monkeypatch):
        get = MagicMock(
            side_effect=[
                _metadata_response(),
                _csv_response(
                    _SD_HEADER + "261;90000.0;70000.0;5000000.5;500.0;14.1\n"
                ),
            ]
        )
        monkeypatch.setattr(public_data.requests, "get", get)

        assert public_data.fetch_sonnendach_municipal() == [
            {
                "bfs_number": 261,
                "total_roof_area_m2": 90000.0,
                "suitable_roof_area_m2": 70000.0,
                "potential_kwh_year": 5000000.5,
                "potential_kwp": 500.0,
                "utilization_pct": 14.1,
            }
        ]

    def test_an_english_sonnendach_row_yields_the_same_record(self, monkeypatch):
        get = MagicMock(
            side_effect=[
                _metadata_response(),
                _csv_response(
                    "bfs_nr;total_roof_area_m2;suitable_roof_area_m2;"
                    "potential_kwh_year;potential_kwp;utilization_pct\n"
                    "295;90000.0;70000.0;5000000.5;500.0;14.1\n"
                ),
            ]
        )
        monkeypatch.setattr(public_data.requests, "get", get)

        assert public_data.fetch_sonnendach_municipal() == [
            {
                "bfs_number": 295,
                "total_roof_area_m2": 90000.0,
                "suitable_roof_area_m2": 70000.0,
                "potential_kwh_year": 5000000.5,
                "potential_kwp": 500.0,
                "utilization_pct": 14.1,
            }
        ]

    def test_missing_sonnendach_fields_read_as_none(self, monkeypatch):
        get = MagicMock(
            side_effect=[_metadata_response(), _csv_response(_SD_HEADER + "261\n")]
        )
        monkeypatch.setattr(public_data.requests, "get", get)

        assert public_data.fetch_sonnendach_municipal() == [
            {
                "bfs_number": 261,
                "total_roof_area_m2": None,
                "suitable_roof_area_m2": None,
                "potential_kwh_year": None,
                "potential_kwp": None,
                "utilization_pct": None,
            }
        ]

    def test_the_municipal_sonnendach_resource_is_preferred_by_name(self, monkeypatch):
        """Alle CSV-Ressourcen im Paket: die gemeinde-spezifische Datei gewinnt."""
        metadata = MagicMock()
        metadata.json.return_value = {
            "result": {
                "resources": [
                    {
                        "format": "CSV",
                        "name": "Gebäudedaten",
                        "url": "https://data.example/buildings.csv",
                    },
                    {
                        "format": "CSV",
                        "name": "Gemeindedaten der Kantone",
                        "url": "https://data.example/municipalities.csv",
                    },
                ]
            }
        }
        get = MagicMock(side_effect=[metadata, _csv_response(_SD_HEADER + "261\n")])
        monkeypatch.setattr(public_data.requests, "get", get)

        public_data.fetch_sonnendach_municipal()
        assert get.call_args_list[1].args == (
            "https://data.example/municipalities.csv",
        )

    @pytest.mark.parametrize(
        "bfs_column",
        ["BFS_NR", "bfs_nr", "gemeinde_bfs"],
    )
    def test_every_bfs_column_alias_is_read(self, monkeypatch, bfs_column):
        header = (
            f"{bfs_column};GEMEINDENAME;anteil_dachflaechen_solar;anteil_ev;"
            "anteil_erneuerbar_heizen;stromverbrauch_mwh;erneuerbare_produktion_mwh;KANTON\n"
        )
        get = MagicMock(
            side_effect=[
                _metadata_response(),
                _csv_response(header + ";1;2;3;4;5\n" + "295;Horgen;1;2;3;4;5;ZH\n"),
            ]
        )
        monkeypatch.setattr(public_data.requests, "get", get)

        assert public_data.fetch_energie_reporter() == [
            {
                "bfs_number": 295,
                "name": "Horgen",
                "kanton": "ZH",
                "solar_potential_pct": 1.0,
                "solar_installed_kwp": None,
                "ev_share_pct": 2.0,
                "renewable_heating_pct": 3.0,
                "electricity_consumption_mwh": 4.0,
                "renewable_production_mwh": 5.0,
            }
        ]

    @pytest.mark.parametrize(
        "bfs_column",
        ["BFS_NR", "bfs_nr", "gemeinde_bfs"],
    )
    def test_every_bfs_column_alias_is_read_for_sonnendach(
        self, monkeypatch, bfs_column
    ):
        get = MagicMock(
            side_effect=[
                _metadata_response(),
                _csv_response(
                    f"{bfs_column};{_SD_DATA_COLUMNS}\n"
                    ";70000.0;70000.0;5000000.5;500.0;14.1\n"
                    "295;90000.0;70000.0;5000000.5;500.0;14.1\n"
                ),
            ]
        )
        monkeypatch.setattr(public_data.requests, "get", get)

        assert public_data.fetch_sonnendach_municipal() == [
            {
                "bfs_number": 295,
                "total_roof_area_m2": 90000.0,
                "suitable_roof_area_m2": 70000.0,
                "potential_kwh_year": 5000000.5,
                "potential_kwp": 500.0,
                "utilization_pct": 14.1,
            }
        ]

    @pytest.mark.parametrize(
        ("fetcher", "message"),
        [
            (
                public_data.fetch_energie_reporter,
                "[PUBLIC_DATA] Energie Reporter CSV schema is incompatible",
            ),
            (
                public_data.fetch_sonnendach_municipal,
                "[PUBLIC_DATA] Sonnendach CSV schema is incompatible",
            ),
        ],
    )
    def test_an_incompatible_csv_names_the_exact_upstream_diagnostics(
        self, monkeypatch, caplog, fetcher, message
    ):
        get = MagicMock(
            side_effect=[
                _metadata_response(),
                _csv_response("upstream-secret-column;OTHER\n1;2\n"),
            ]
        )
        monkeypatch.setattr(public_data.requests, "get", get)

        with caplog.at_level(logging.ERROR):
            assert fetcher() is None

        assert any(item.getMessage() == message for item in caplog.records), (
            caplog.messages
        )
        assert "upstream-secret-column" not in caplog.text


class TestBulkFetcherFailureLogs:
    @pytest.mark.parametrize(
        ("fetcher", "missing"),
        [
            (public_data.fetch_energie_reporter, "resources"),
            (public_data.fetch_energie_reporter, "result"),
            (public_data.fetch_sonnendach_municipal, "resources"),
            (public_data.fetch_sonnendach_municipal, "result"),
        ],
    )
    def test_a_package_without_resources_reports_the_source_diagnostic(
        self, monkeypatch, caplog, fetcher, missing
    ):
        metadata = MagicMock()
        payload = {"result": {"resources": []}}
        if missing == "result":
            payload = {"other": True}
        elif missing == "resources":
            payload["result"] = {}
        metadata.json.return_value = payload
        get = MagicMock(side_effect=[metadata, RuntimeError("never reached")])
        monkeypatch.setattr(public_data.requests, "get", get)

        with caplog.at_level(logging.WARNING):
            assert fetcher() is None

        expected_warning = {
            public_data.fetch_energie_reporter: (
                "[PUBLIC_DATA] Energie Reporter: no supported resource found"
            ),
            public_data.fetch_sonnendach_municipal: (
                "[PUBLIC_DATA] Sonnendach: no CSV resource found"
            ),
        }
        assert [item.getMessage() for item in caplog.records] == [
            expected_warning[fetcher]
        ], caplog.messages
        assert "fetch failed" not in caplog.text

    @pytest.mark.parametrize(
        ("fetcher", "url"),
        [
            (public_data.fetch_energie_reporter, public_data.ENERGIE_REPORTER_URL),
            (public_data.fetch_sonnendach_municipal, public_data.SONNENDACH_URL),
        ],
    )
    def test_an_upstream_failure_reports_the_exact_operator_error(
        self, monkeypatch, caplog, fetcher, url
    ):
        def fail(_url, **_kwargs):
            raise RuntimeError("upstream down")

        monkeypatch.setattr(public_data.requests, "get", fail)

        with caplog.at_level(logging.ERROR):
            assert fetcher() is None

        assert "upstream down" not in caplog.text
        expected = {
            public_data.fetch_energie_reporter: (
                "[PUBLIC_DATA] Energie Reporter fetch failed"
            ),
            public_data.fetch_sonnendach_municipal: (
                "[PUBLIC_DATA] Sonnendach fetch failed"
            ),
        }
        assert any(message == expected[fetcher] for message in caplog.messages), (
            caplog.messages
        )

    def test_the_elcom_success_and_failure_lines_name_their_numbers(self, caplog):
        from public_data import fetch_elcom_tariffs

        response = MagicMock()
        response.json.return_value = {"results": {"bindings": []}}
        with (
            patch("public_data.requests.post", MagicMock(return_value=response)),
            caplog.at_level(logging.INFO),
        ):
            assert fetch_elcom_tariffs(261, 2026) == []

        assert any(
            item.getMessage()
            == "[PUBLIC_DATA] ElCom: 0 tariff records for BFS 261/2026"
            for item in caplog.records
        ), caplog.messages

        with (
            patch(
                "public_data.requests.post", MagicMock(side_effect=RuntimeError("down"))
            ),
            caplog.at_level(logging.ERROR),
        ):
            assert fetch_elcom_tariffs(261, 2026) is None

        assert any(
            item.getMessage() == "[PUBLIC_DATA] ElCom fetch failed for BFS 261"
            for item in caplog.records
        ), caplog.messages


class TestComputeTransitionScore:
    """Test energy transition score computation."""

    def test_full_score(self):
        from public_data import compute_energy_transition_score

        profile = {
            "solar_potential_pct": 100,
            "ev_share_pct": 30,
            "renewable_heating_pct": 100,
            "electricity_consumption_mwh": 100,
            "renewable_production_mwh": 100,
        }
        score = compute_energy_transition_score(profile)
        assert score == 100.0

    def test_zero_score(self):
        from public_data import compute_energy_transition_score

        score = compute_energy_transition_score({})
        assert score == 0.0

    def test_partial_score(self):
        from public_data import compute_energy_transition_score

        profile = {
            "solar_potential_pct": 50,
            "ev_share_pct": 15,
            "renewable_heating_pct": 50,
            "electricity_consumption_mwh": 200,
            "renewable_production_mwh": 50,
        }
        score = compute_energy_transition_score(profile)
        assert 0 < score < 100
        # solar: 50/100 * 30 = 15
        # ev: 15/30 * 20 = 10
        # heating: 50/100 * 25 = 12.5
        # prod: 50/200 * 25 = 6.25
        assert score == 43.8  # 15 + 10 + 12.5 + 6.25 rounded


class TestFetchElcom:
    """Test ElCom SPARQL fetcher (mocked HTTP)."""

    @patch("public_data.requests.post")
    def test_fetch_success(self, mock_post):
        from public_data import fetch_elcom_tariffs

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": {
                "bindings": [
                    {
                        "operator": {"value": "EKZ"},
                        "category": {"value": "H4"},
                        "total": {"value": "27.5"},
                        "energy": {"value": "12.0"},
                        "grid": {"value": "9.5"},
                    }
                ]
            }
        }
        mock_post.return_value = mock_resp
        result = fetch_elcom_tariffs(261, 2026)
        assert len(result) == 1
        assert result[0]["operator_name"] == "EKZ"
        assert result[0]["total_rp_kwh"] == 27.5

    @patch("public_data.requests.post")
    def test_fetch_empty(self, mock_post):
        from public_data import fetch_elcom_tariffs

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": {"bindings": []}}
        mock_post.return_value = mock_resp
        assert fetch_elcom_tariffs(999, 2026) == []

    @patch("public_data.requests.post")
    def test_fetch_network_error(self, mock_post, caplog):
        from public_data import fetch_elcom_tariffs

        secret = "token=elcom-upstream-secret"
        mock_post.side_effect = Exception(secret)
        with caplog.at_level(logging.ERROR):
            assert fetch_elcom_tariffs(261, 2026) is None
        assert secret not in caplog.text


class TestSafeHelpers:
    def test_safe_int(self):
        from public_data import _safe_int

        assert _safe_int("42") == 42
        assert _safe_int(42) == 42
        assert _safe_int(None) is None
        assert _safe_int("abc") is None

    def test_safe_float(self):
        from public_data import _safe_float

        assert _safe_float("3.14") == 3.14
        assert _safe_float("3,14") == 3.14
        assert _safe_float(None) is None
        assert _safe_float("abc") is None
