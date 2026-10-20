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

    network_calls = []

    def get(url, **kwargs):
        network_calls.append((url, kwargs))
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
    assert network_calls == [
        (public_data.ENERGIE_REPORTER_URL, {"timeout": 15}),
        (
            "https://data.example.invalid/energyreporter_latest.zip",
            {"timeout": 30, "stream": True},
        ),
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
        "ambiguous_municipal_nested",
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
    elif failure == "ambiguous_municipal_nested":
        files["releases/latest/energyreporter_municipality_latest.csv"] = municipal
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


@pytest.mark.parametrize("boundary", ["compressed", "expanded", "members", "municipal"])
@pytest.mark.parametrize("excess", [0, 1], ids=["at-limit", "one-over-limit"])
def test_reporter_archive_limits_accept_boundary_and_reject_one_over(
    monkeypatch, boundary, excess
):
    municipal = (
        "bfs_nr,municipality,canton,electric_car_share,solar_potential_usage,"
        "renewable_heating_share,elec_consumption_mwh_per_year,"
        "renelec_production_mwh_per_year\n"
        "99971,Synthetic OW,OW,0.1,0.2,0.3,400,150\n"
    )
    files = {"energyreporter_municipality_latest.csv": municipal}
    compression = zipfile.ZIP_DEFLATED

    def build():
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w", compression) as archive:
            for name, body in files.items():
                archive.writestr(name, body)
        return content.getvalue()

    if boundary == "members":
        files.update({f"notes-{index}.txt": "" for index in range(99 + excess)})
    elif boundary == "expanded":
        files["notes.txt"] = "x" * (20 * 1024 * 1024 + excess - len(municipal))
    elif boundary == "municipal":
        files["energyreporter_municipality_latest.csv"] += "\n" * (
            10 * 1024 * 1024 + excess - len(municipal)
        )
    else:
        compression = zipfile.ZIP_STORED
        files["notes.txt"] = ""
        files["notes.txt"] = "x" * (5 * 1024 * 1024 + excess - len(build()))
    body = build()
    if boundary == "compressed":
        assert len(body) == 5 * 1024 * 1024 + excess

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
    result = public_data.fetch_energie_reporter()
    if excess:
        assert result is None
    else:
        assert result is not None and len(result) == 1
        assert result[0]["bfs_number"] == 99971
        assert result[0]["name"] == "Synthetic OW"
        assert result[0]["ev_share_pct"] == 10


def test_reporter_stops_reading_an_oversized_archive_before_consuming_it_all(
    monkeypatch,
):
    from urllib3.response import HTTPResponse

    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("notes.txt", b"x" * (8 * 1024 * 1024))

    class CountedStream(io.BytesIO):
        consumed = 0

        def read(self, size=-1):
            block = super().read(size)
            self.consumed += len(block)
            return block

    source = CountedStream(content.getvalue())
    response = requests.Response()
    response.status_code = 200
    response.raw = HTTPResponse(body=source, preload_content=False)

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
        return response

    monkeypatch.setattr(requests, "get", get)
    assert public_data.fetch_energie_reporter() is None
    # Allow a full MiB of read-ahead beyond the 5 MiB download cap. The
    # fetcher must reject this delivery before consuming the entire 8 MiB.
    assert 5 * 1024 * 1024 < source.consumed <= 6 * 1024 * 1024
    assert source.closed


@pytest.mark.parametrize("installed_kwp", [1200, 0])
def test_refresh_persists_reporter_installed_pv_before_potential(
    monkeypatch, installed_kwp
):
    import database
    import municipality_profile

    header = (
        "bfs_nr,municipality,canton,electric_car_share,solar_potential_usage,"
        "solar_power_installed_kwp,renewable_heating_share,"
        "elec_consumption_mwh_per_year,renelec_production_mwh_per_year\n"
    )
    archive = _archive(
        {
            "energyreporter_municipality_latest.csv": header
            + f"99971,Synthetic OW,OW,0.125,0.25,{installed_kwp},0.5,400,150\n"
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
                                    "url": "https://data.example.invalid/energyreporter_latest.zip",
                                }
                            ]
                        }
                    }
                ).encode()
            )
        assert url == "https://data.example.invalid/energyreporter_latest.zip"
        return _response(archive)

    saved = []
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(public_data, "fetch_elcom_tariffs", lambda *_args: [])
    monkeypatch.setattr(
        database,
        "get_municipality_profile",
        lambda _bfs: {"name": "Old", "kanton": "OW", "solar_installed_kwp": 222},
    )
    monkeypatch.setattr(
        database,
        "save_municipality_profile",
        lambda profile: saved.append(dict(profile)),
    )
    monkeypatch.setattr(
        municipality_profile, "invalidate_profile_cache", lambda _bfs: None
    )

    result = public_data.refresh_municipality(
        99971, _sonnendach=[{"bfs_number": 99971, "potential_kwp": 999}]
    )

    assert result["profile"]["solar_installed_kwp"] == installed_kwp
    assert len(saved) == 1
    assert saved[0]["solar_installed_kwp"] == installed_kwp
