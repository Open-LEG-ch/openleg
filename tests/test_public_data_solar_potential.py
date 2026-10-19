# SPDX-License-Identifier: AGPL-3.0-or-later
"""BFE municipal generation potential through the public fetcher boundary."""

import json

import requests

import public_data


def _response(body):
    response = requests.Response()
    response.status_code = 200
    response._content = body.encode()
    response.encoding = "utf-8"
    return response


def test_bfe_roof_potential_converts_gwh_without_inventing_capacity(monkeypatch):
    def get(url, **kwargs):
        if url == public_data.SONNENDACH_URL:
            return _response(
                json.dumps(
                    {
                        "result": {
                            "resources": [
                                {
                                    "format": "CSV",
                                    "name": {"de": "Solarenergiepotenziale"},
                                    "url": "https://data.example.invalid/solar.csv",
                                }
                            ]
                        }
                    }
                )
            )
        assert url == "https://data.example.invalid/solar.csv"
        return _response(
            "MunicipalityNumber,MunicipalityName,Country,"
            "Scenario1_RoofsOnly_PotentialSolarElectricity_GWh,"
            "Scenario3_RoofsFacades_PotentialSolarElectricity_GWh\n"
            "99971,Synthetic OW,CH,1.25,2.75\n"
        )

    monkeypatch.setattr(requests, "get", get)

    assert public_data.fetch_sonnendach_municipal() == [
        {
            "bfs_number": 99971,
            "total_roof_area_m2": None,
            "suitable_roof_area_m2": None,
            "potential_kwh_year": 1_250_000,
            "potential_kwp": None,
            "utilization_pct": None,
        }
    ]


def test_bfe_municipal_delivery_remains_available_during_metadata_outage(monkeypatch):
    def get(url, **kwargs):
        if url == public_data.SONNENDACH_URL:
            response = _response("unavailable metadata")
            response.status_code = 403
            return response
        assert url == (
            "https://www.uvek-gis.admin.ch/BFE/ogd/52/"
            "Solarenergiepotenziale_Gemeinden_Daecher_und_Fassaden.csv"
        )
        return _response(
            "MunicipalityNumber,Scenario1_RoofsOnly_PotentialSolarElectricity_GWh\n"
            "99971,1.25\n"
        )

    monkeypatch.setattr(requests, "get", get)

    result = public_data.fetch_sonnendach_municipal()
    assert result is not None
    assert len(result) == 1
    assert result[0]["bfs_number"] == 99971
    assert result[0]["potential_kwh_year"] == 1_250_000
