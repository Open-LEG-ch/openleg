# SPDX-License-Identifier: AGPL-3.0-or-later
"""Synthetic Solar API responses; no installation is contacted."""

import json
import subprocess
import sys
import time
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

import pytest

from telemetry_fronius import decode_common


def response():
    return {
        "Head": {
            "RequestArguments": {
                "DataCollection": "CommonInverterData",
                "DeviceClass": "Inverter",
                "DeviceId": "1",
                "Scope": "Device",
            },
            "Status": {"Code": 0},
            "Timestamp": "2026-10-19T12:00:00+02:00",
        },
        "Body": {
            "Data": {
                "PAC": {"Value": 84, "Unit": "W"},
                "TOTAL_ENERGY": {"Value": 1734796, "Unit": "Wh"},
                "DeviceStatus": {"ErrorCode": 0, "StatusCode": 7},
                "future_vendor_field": {"Value": 12},
            }
        },
    }


def test_logger_response_time_never_becomes_a_measurement_time():
    payload = response()
    original = deepcopy(payload)
    result = decode_common(payload, device_id=1, pv_only=True)
    assert result["responded_at"] == "2026-10-19T10:00:00+00:00"
    assert result["state"] == "available"
    assert result["samples"] == [
        {
            "metric": "power",
            "unit": "W",
            "value": "84",
            "direction": "generation",
            "measurement_location": "pv_inverter",
            "quality": "measured",
            "observed_at": None,
        },
        {
            "metric": "energy_counter",
            "unit": "Wh",
            "value": "1734796",
            "direction": "generation",
            "measurement_location": "pv_inverter",
            "quality": "measured",
            "observed_at": None,
        },
    ]
    assert result["source_data"] == original["Body"]["Data"]
    assert payload == original


@pytest.mark.parametrize("watts", [0, -12])
def test_night_zero_and_consumption_keep_their_original_sign(watts):
    payload = response()
    payload["Body"]["Data"]["PAC"]["Value"] = watts
    result = decode_common(payload, device_id=1, pv_only=True)
    power = [sample for sample in result["samples"] if sample["metric"] == "power"]
    assert [sample["value"] for sample in power] == (["0"] if watts == 0 else [])
    assert result["source_data"]["PAC"]["Value"] == watts


def test_unconfirmed_topology_does_not_label_ac_power_as_pv():
    result = decode_common(response(), device_id=1)
    assert result["samples"] == []
    assert result["source_data"]["PAC"]["Value"] == 84


def test_missing_power_is_not_a_zero_and_device_fault_is_not_collector_loss():
    payload = response()
    del payload["Body"]["Data"]["PAC"]
    result = decode_common(payload, device_id=1, pv_only=True)
    assert result["state"] == "partial"
    assert all(sample["metric"] != "power" for sample in result["samples"])
    payload["Body"]["Data"]["DeviceStatus"]["ErrorCode"] = 102
    fault = decode_common(payload, device_id=1, pv_only=True)
    assert fault["state"] == "device_fault"
    assert fault["source_data"]["DeviceStatus"]["ErrorCode"] == 102
    assert fault["samples"] == []


@pytest.mark.parametrize(
    "damage",
    [
        lambda p: p["Head"]["RequestArguments"].update(DeviceId="2"),
        lambda p: p["Head"]["RequestArguments"].update(Scope="System"),
        lambda p: p["Head"]["RequestArguments"].update(
            DataCollection="MinMaxInverterData"
        ),
        lambda p: p["Head"]["Status"].update(Code=5),
        lambda p: p["Body"]["Data"]["PAC"].update(Unit="kW"),
        lambda p: p["Body"]["Data"]["PAC"].update(Value=True),
        lambda p: p["Body"]["Data"]["PAC"].update(Value=float("nan")),
        lambda p: p["Body"]["Data"]["TOTAL_ENERGY"].update(Value=-1),
    ],
)
def test_invalid_responses_cannot_become_observations(damage):
    payload = response()
    damage(payload)
    with pytest.raises(ValueError, match="invalid_fronius_response"):
        decode_common(payload, device_id=1, pv_only=True)


@pytest.mark.parametrize("payload", [None, [], {}, {"Head": {"Status": []}}])
def test_malformed_responses_fail_with_a_safe_diagnostic(payload):
    with pytest.raises(ValueError, match="invalid_fronius_response"):
        decode_common(payload, device_id=1, pv_only=True)


def test_imprecise_optional_counter_does_not_discard_power_or_round_the_source():
    payload = response()
    payload["Body"]["Data"]["TOTAL_ENERGY"]["Value"] = Decimal("1734796.1200000001")
    result = decode_common(payload, device_id=1, pv_only=True)
    assert result["state"] == "partial"
    assert [sample["metric"] for sample in result["samples"]] == ["power"]
    assert result["omitted_channels"] == {"TOTAL_ENERGY": "unsupported_precision"}
    assert result["source_data"]["TOTAL_ENERGY"]["Value"] == Decimal(
        "1734796.1200000001"
    )


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "100.64.0.1",
        "8.8.8.8",
        "224.0.0.1",
        "::1",
        "inverter.local",
        "http://192.168.1.2",
        "192.168.1.2:8080",
        "192.168.1.2/path",
        "192.168.1.2@127.0.0.1",
        3232248322,
        None,
    ],
)
def test_enrollment_rejects_every_destination_except_a_literal_lan_address(address):
    from telemetry_fronius import FroniusReader

    calls = []
    with pytest.raises(ValueError, match="invalid_fronius_address"):
        FroniusReader(address, 1, get=lambda url: calls.append(url))
    assert calls == []


def test_only_enrolled_fixed_reads_run_with_spacing_backoff_and_recovery():
    from telemetry_fronius import FroniusReader

    calls = []
    now = [0.0]
    replies = [
        {"APIVersion": 1, "BaseURL": "http://169.254.169.254/control"},
        TimeoutError(),
        response(),
    ]

    def get(url):
        calls.append(url)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    reader = FroniusReader(
        "192.168.50.2", 1, pv_only=True, get=get, clock=lambda: now[0]
    )
    assert reader.poll()["state"] == "api_confirmed"
    assert reader.poll()["state"] == "backoff"
    assert len(calls) == 1
    now[0] = 4
    unavailable = reader.poll()
    assert unavailable["state"] == "unavailable" and unavailable["samples"] == []
    now[0] = 5
    assert reader.poll()["state"] == "backoff"
    assert len(calls) == 2
    now[0] = 34
    recovered = reader.poll()
    assert recovered["state"] == "available"
    assert recovered["samples"][0]["observed_at"] is None
    assert calls == [
        "http://192.168.50.2/solar_api/GetAPIVersion.cgi",
        "http://192.168.50.2/solar_api/v1/GetInverterRealtimeData.cgi?Scope=Device&DeviceId=1&DataCollection=CommonInverterData",
        "http://192.168.50.2/solar_api/v1/GetInverterRealtimeData.cgi?Scope=Device&DeviceId=1&DataCollection=CommonInverterData",
    ]
    now[0] = 35
    assert reader.poll()["state"] == "backoff"
    assert len(calls) == 3


@pytest.mark.parametrize(
    "mode",
    [
        "redirect",
        "oversize",
        "duplicate",
        "compressed",
        "invalid_chunk",
        "slow_headers",
        "slow_body",
        "ok",
    ],
)
def test_actual_http_transport_is_bounded_and_ignores_environment_proxies(
    monkeypatch, mode
):
    import telemetry_fronius as adapter

    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            if self.path == "/solar_api/GetAPIVersion.cgi":
                raw = b'{"APIVersion":1}'
            elif mode == "redirect" and self.path != "/device-management":
                raw = json.dumps(response()).encode()
                self.send_response(307)
                self.send_header("Location", "/device-management")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            elif mode == "invalid_chunk":
                self.wfile.write(
                    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\nnot-a-chunk\r\n"
                )
                return
            elif mode in {"slow_headers", "slow_body"}:
                raw = json.dumps(response()).encode()
                try:
                    self.wfile.write(b"HTTP/1.0 200 OK\r\n")
                    if mode == "slow_headers":
                        for _ in range(30):
                            self.wfile.write(b"X-Slow: yes\r\n")
                            time.sleep(0.05)
                    self.wfile.write(f"Content-Length: {len(raw)}\r\n\r\n".encode())
                    if mode == "slow_body":
                        for byte in raw:
                            self.wfile.write(bytes([byte]))
                            time.sleep(0.05)
                    else:
                        self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            elif mode == "oversize":
                raw = json.dumps(response()).encode()
                raw += b" " * (adapter.MAX_RESPONSE_BYTES + 1 - len(raw))
            elif mode == "duplicate":
                raw = b'{"Head":{},"Head":{}}'
            else:
                raw = json.dumps(response()).encode()
            self.send_response(200)
            if mode == "compressed" and self.path != "/solar_api/GetAPIVersion.cgi":
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    real_get = adapter._get

    def route_to_fixture(url):
        assert url.startswith("http://192.168.50.2/")
        return real_get(
            url.replace("http://192.168.50.2", f"http://127.0.0.1:{server.server_port}")
        )

    monkeypatch.setattr(adapter, "_get", route_to_fixture)
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setattr(adapter, "REQUEST_BUDGET_SECONDS", 0.25)
    now = [0.0]
    try:
        reader = adapter.FroniusReader(
            "192.168.50.2", 1, pv_only=True, clock=lambda: now[0]
        )
        assert reader.poll()["state"] == "api_confirmed"
        now[0] = 4
        started = time.monotonic()
        result = reader.poll()
        assert time.monotonic() - started < 1
        assert result["state"] == ("available" if mode == "ok" else "unavailable")
        assert len(paths) == 2
        assert all(path.startswith("/solar_api/") for path in paths)
        assert "/device-management" not in paths
        if mode != "ok":
            assert result["samples"] == []
            assert result["retry_after"] == 30
            assert reader.poll()["state"] == "backoff"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_offline_cli_queues_counter_resets_without_inventing_measurement_age(tmp_path):
    import telemetry
    from telemetry_collector import Collector
    from tests.test_telemetry_collector import config

    local = config(tmp_path, endpoint=None, credential=None)
    config_path = tmp_path / "collector.json"
    config_path.write_text(json.dumps(local))
    config_path.chmod(0o600)
    script = Path(__file__).resolve().parents[1] / "telemetry_fronius.py"
    for counter in (1000, 1):
        payload = response()
        payload["Body"]["Data"]["TOTAL_ENERGY"]["Value"] = counter
        completed = subprocess.run(
            [
                sys.executable,
                str(script),
                "--device-id",
                "1",
                "--pv-only",
                "--collector-config",
                str(config_path),
            ],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=True,
        )
        report = json.loads(completed.stdout)
        assert report["queued_count"] == 2
        assert "source_data" not in report
        assert "future_vendor_field" not in completed.stdout
    sent = []

    def send(endpoint, token, body):
        sent.append(json.loads(body))
        return 201, b'{"schema_version":"telemetry/1","accepted":4,"duplicates":0}'

    collector = Collector(config(tmp_path), send=send)
    try:
        assert collector.flush_once()["state"] == "sent"
    finally:
        collector.close()
    samples = sent[0]["samples"]
    assert [
        sample["value"] for sample in samples if sample["metric"] == "energy_counter"
    ] == ["1000", "1"]
    assert all(sample["metric"] != "interval_energy" for sample in samples)
    assert [sample["sequence"] for sample in samples] == [1, 2, 3, 4]
    assert all(
        telemetry.freshness(sample, datetime(2026, 10, 20, tzinfo=UTC)) == "unknown"
        for sample in samples
    )
