# SPDX-License-Identifier: AGPL-3.0-or-later
"""Published Energie Reporter archives through the public fetcher boundary."""

import io
import json
import zipfile

import pytest
import requests

import public_data


def _response(body):
    response = requests.Response()
    response.status_code = 200
    response._content = body
    response._content_consumed = True
    response.encoding = "utf-8"
    return response


def _archive(files):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return content.getvalue()


def test_reporter_latest_archive_maps_published_municipal_units(monkeypatch):
    header = (
        "bfs_nr,municipality,canton,electric_car_share,solar_potential_usage,"
        "solar_power_installed_kwp,renewable_heating_share,"
        "elec_consumption_mwh_per_year,renelec_production_mwh_per_year\n"
    )
    archive = _archive(
        {
            "energyreporter_canton_latest.csv": header
            + "99972,Not a municipal row,OW,0.1,0.2,99,0.3,100,50\n",
            "energyreporter_municipality_latest.csv": header
            + "99971,Synthetic OW,OW,0.125,0.25,1200,0.5,400,150\n",
        }
    )

    def get(url, **kwargs):
        if url == public_data.ENERGIE_REPORTER_URL:
            return _response(
                json.dumps(
                    {
                        "result": {
                            "resources": [
                                {
                                    "format": "ZIP",
                                    "url": "https://data.example.invalid/energyreporter_historized.zip",
                                },
                                {
                                    "format": "ZIP",
                                    "url": "https://data.example.invalid/energyreporter_latest.zip",
                                },
                            ]
                        }
                    }
                ).encode()
            )
        assert url == "https://data.example.invalid/energyreporter_latest.zip"
        return _response(archive)

    monkeypatch.setattr(requests, "get", get)

    assert public_data.fetch_energie_reporter() == [
        {
            "bfs_number": 99971,
            "name": "Synthetic OW",
            "kanton": "OW",
            "solar_potential_pct": 25,
            "solar_installed_kwp": 1200,
            "ev_share_pct": 12.5,
            "renewable_heating_pct": 50,
            "electricity_consumption_mwh": 400,
            "renewable_production_mwh": 150,
        }
    ]


def test_reporter_uses_the_published_archive_when_metadata_is_unavailable(monkeypatch):
    archive = _archive(
        {
            "energyreporter_municipality_latest.csv": (
                "bfs_nr,municipality,canton,electric_car_share,solar_potential_usage,"
                "renewable_heating_share,elec_consumption_mwh_per_year,"
                "renelec_production_mwh_per_year\n"
                "99971,Synthetic OW,OW,0.1,0.2,0.3,400,150\n"
            )
        }
    )

    def get(url, **kwargs):
        if url == public_data.ENERGIE_REPORTER_URL:
            response = _response(b"metadata unavailable")
            response.status_code = 403
            return response
        assert (
            url
            == "https://opendata.geoimpact.ch/energiereporter/energyreporter_latest.zip"
        )
        return _response(archive)

    monkeypatch.setattr(requests, "get", get)

    result = public_data.fetch_energie_reporter()
    assert result is not None
    assert len(result) == 1
    assert result[0]["bfs_number"] == 99971
    assert result[0]["ev_share_pct"] == 10


@pytest.mark.parametrize(
    "failure",
    [
        "invalid_zip",
        "missing_municipal",
        "ambiguous_municipal",
        "compressed_limit",
        "expanded_limit",
        "member_limit",
        "municipal_limit",
    ],
)
def test_reporter_rejects_unsupported_or_excessive_archives(monkeypatch, failure):
    municipal = (
        "bfs_nr,municipality,canton,electric_car_share,solar_potential_usage,"
        "renewable_heating_share,elec_consumption_mwh_per_year,"
        "renelec_production_mwh_per_year\n"
        "99971,Synthetic OW,OW,0.1,0.2,0.3,400,150\n"
    )
    files = {"energyreporter_municipality_latest.csv": municipal}
    if failure == "missing_municipal":
        files = {"energyreporter_canton_latest.csv": municipal}
    elif failure == "ambiguous_municipal":
        files["nested/energyreporter_municipality_latest.csv"] = municipal
    elif failure == "compressed_limit":
        files["unneeded.bin"] = "x" * (5 * 1024 * 1024)
    elif failure == "expanded_limit":
        files["unneeded.bin"] = "x" * (21 * 1024 * 1024)
    elif failure == "member_limit":
        files.update({f"unneeded-{index}.txt": "" for index in range(100)})
    elif failure == "municipal_limit":
        files["energyreporter_municipality_latest.csv"] += "\n" * (10 * 1024 * 1024)
    content = io.BytesIO()
    compression = (
        zipfile.ZIP_STORED if failure == "compressed_limit" else zipfile.ZIP_DEFLATED
    )
    with zipfile.ZipFile(content, "w", compression) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    body = b"invalid archive" if failure == "invalid_zip" else content.getvalue()

    def get(url, **kwargs):
        if url == public_data.ENERGIE_REPORTER_URL:
            return _response(
                json.dumps(
                    {
                        "result": {
                            "resources": [
                                {
                                    "format": "ZIP",
                                    "url": "https://data.example.invalid/energyreporter_latest.zip",
                                }
                            ]
                        }
                    }
                ).encode()
            )
        return _response(body)

    monkeypatch.setattr(requests, "get", get)

    assert public_data.fetch_energie_reporter() is None
