# SPDX-License-Identifier: AGPL-3.0-or-later
"""Official HTTPS boundary: TLS, redirects, size and transfer deadlines."""

import io
import ssl
from types import SimpleNamespace

import pytest

import solar_forecast as forecast
import solar_forecast_cache as weather


class Response:
    def __init__(self, body=b"public data", status=200, headers=None):
        self.status = status
        self.headers = headers or {}
        self.body = io.BytesIO(body)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read1(self, size):
        return self.body.read(size)


@pytest.fixture
def upstream(monkeypatch):
    state = SimpleNamespace(
        response=Response(), closed=False, requests=[], connections=[]
    )

    class Connection:
        def __init__(self, host, *, timeout, context):
            state.connections.append((host, timeout, context))
            self.sock = SimpleNamespace(shutdown=lambda *args: None)

        def connect(self):
            pass

        def request(self, method, target, *, headers):
            state.requests.append((method, target, headers))

        def getresponse(self):
            return state.response

        def close(self):
            state.closed = True

    monkeypatch.setattr(weather, "HTTPSConnection", Connection)
    return state


def test_download_uses_verified_direct_https_and_ignores_proxy_environment(
    upstream, monkeypatch
):
    monkeypatch.setenv("HTTPS_PROXY", "http://unselected.invalid:8080")
    assert weather._download(weather.COLLECTION_URL, 100) == b"public data"
    host, timeout, context = upstream.connections[0]
    assert host == "data.geo.admin.ch" and timeout == 5
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert upstream.requests == [
        (
            "GET",
            "/api/stac/v1/collections/ch.meteoschweiz.ogd-local-forecasting",
            {"Accept-Encoding": "identity"},
        )
    ]
    assert upstream.closed


@pytest.mark.parametrize(
    "url",
    [
        "http://data.geo.admin.ch/data.csv",
        "https://example.com/data.csv",
        weather.COLLECTION_URL + "?lat=47&lon=8",
    ],
)
def test_download_rejects_unselected_urls_before_connecting(upstream, url):
    with pytest.raises(forecast.ForecastError):
        weather._download(url, 100)
    assert upstream.connections == []


@pytest.mark.parametrize(
    "response",
    [
        Response(status=302, headers={"Location": weather.COLLECTION_URL}),
        Response(headers={"Content-Encoding": "gzip"}),
        Response(headers={"Content-Length": "101"}),
    ],
)
def test_redirects_compression_and_declared_oversize_remain_unavailable(
    upstream, response
):
    upstream.response = response
    with pytest.raises(forecast.ForecastError):
        weather._download(weather.COLLECTION_URL, 100)
    assert upstream.closed
    assert len(upstream.requests) == 1


def test_stream_limit_is_enforced_without_a_content_length(upstream):
    upstream.response = Response(body=b"abcd")
    with pytest.raises(forecast.ForecastError, match="too_large"):
        weather._download(weather.COLLECTION_URL, 3)
    assert upstream.closed


def test_body_transfer_does_not_extend_deadline(upstream, monkeypatch):
    elapsed = [0]
    monkeypatch.setattr(weather.time, "monotonic", lambda: elapsed[0])
    original_read = upstream.response.read1

    def delayed_read(size):
        elapsed[0] = 21
        return original_read(size)

    upstream.response.read1 = delayed_read
    with pytest.raises(TimeoutError):
        weather._download(weather.COLLECTION_URL, 100)
    assert upstream.closed
