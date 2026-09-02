# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for public_data.py: fetchers and computed metrics."""

import csv
import logging
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
