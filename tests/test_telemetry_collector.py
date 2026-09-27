# SPDX-License-Identifier: AGPL-3.0-or-later
"""Offline bounds and outgoing-request safety without devices or external hosts."""

import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event, Thread

import pytest

import telemetry_collector as collector_module
from telemetry_collector import Collector, configuration, load_configuration

INSTALLATION = "c3fc9e27-60af-460b-8b83-ec81d533dcb9"
ENDPOINT = f"https://owner.example/api/telemetry/v1/communities/community-a/installations/{INSTALLATION}/samples"


def config(tmp_path, **overrides):
    return {
        "installation_id": INSTALLATION,
        "source": {
            "name": "Roof",
            "device_id": "inverter",
            "source_id": "local",
            "cadence_seconds": 30,
        },
        "spool": str(tmp_path / "private" / "queue.sqlite3"),
        "endpoint": ENDPOINT,
        "allowed_endpoints": [ENDPOINT],
        "credential": "olt_" + "a" * 43,
        **overrides,
    }


def observation(**overrides):
    return {
        "metric": "power",
        "unit": "W",
        "value": 10,
        "direction": "generation",
        "measurement_location": "pv_inverter",
        "quality": "measured",
        "observed_at": None,
        **overrides,
    }


def test_local_only_never_contacts_a_host_and_queue_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(collector_module, "MAX_QUEUE", 2)
    now = [100000.0]

    def forbidden(*args):
        pytest.fail("local-only collector made a request")

    collector = Collector(
        config(tmp_path, endpoint=None, credential=None),
        send=forbidden,
        clock=lambda: now[0],
    )
    try:
        assert collector.enqueue(observation()) == 1
        assert collector.enqueue(observation()) == 2
        with pytest.raises(ValueError, match="queue_full"):
            collector.enqueue(observation())
        assert collector.flush_once()["state"] == "local_only"
        now[0] += collector_module.MAX_AGE + 1
        assert collector.flush_once()["expired"] == 2
        assert collector.enqueue(observation()) == 3
        assert os.stat(collector.config["spool"]).st_mode & 0o077 == 0
    finally:
        collector.close()


def test_backoff_persists_and_acknowledged_retries_preserve_bytes(tmp_path):
    now = [100000.0]
    requests = []

    def send(endpoint, token, body):
        requests.append(body)
        return (
            (503, b"")
            if len(requests) == 1
            else (201, b'{"schema_version":"telemetry/1","accepted":0,"duplicates":1}')
        )

    collector = Collector(config(tmp_path), send=send, clock=lambda: now[0])
    collector.enqueue(observation())
    assert collector.flush_once()["state"] == "retry"
    collector.close()
    collector = Collector(config(tmp_path), send=send, clock=lambda: now[0])
    try:
        assert collector.flush_once()["state"] == "backoff"
        assert len(requests) == 1
        now[0] += 2
        assert collector.flush_once()["state"] == "sent"
        assert requests[0] == requests[1]
        assert json.loads(requests[0])["samples"][0]["observed_at"] is None
        assert collector.flush_once()["state"] == "empty"
        assert collector.enqueue(observation()) == 2
    finally:
        collector.close()


@pytest.mark.parametrize("status", [301, 302, 307, 308, 400, 401, 403, 404, 409, 413])
def test_rejected_batches_stop_automatic_forwarding(tmp_path, status):
    calls = []

    def send(*args):
        calls.append(1)
        return status, b""

    collector = Collector(config(tmp_path), send=send)
    collector.enqueue(observation())
    assert collector.flush_once()["state"] == "blocked"
    collector.close()
    collector = Collector(config(tmp_path), send=send)
    try:
        assert collector.flush_once()["state"] == "blocked"
        assert calls == [1]
    finally:
        collector.close()


def test_retries_are_finite_and_delay_is_capped(tmp_path):
    now = [100000.0]
    collector = Collector(
        config(tmp_path), send=lambda *args: (429, b""), clock=lambda: now[0]
    )
    try:
        collector.enqueue(observation())
        for attempt in range(collector_module.MAX_ATTEMPTS):
            response = collector.flush_once()
            assert response["state"] == (
                "blocked" if attempt == collector_module.MAX_ATTEMPTS - 1 else "retry"
            )
            due = collector.db.execute("SELECT next_attempt FROM state").fetchone()[0]
            assert now[0] < due <= now[0] + 300
            now[0] = due
        assert collector.flush_once()["state"] == "blocked"
        collector.resume()
        assert collector.flush_once()["state"] == "retry"
    finally:
        collector.close()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://owner.example/x",
        ENDPOINT + "?token=secret",
        ENDPOINT + "#fragment",
        ENDPOINT.replace("https://", "https://user:password@"),
        ENDPOINT.replace("/samples", "/command"),
        ENDPOINT.replace(INSTALLATION, "00000000-0000-0000-0000-000000000000"),
        ENDPOINT.replace("https://owner.example", "http://192.168.1.2"),
        ENDPOINT.replace("https://owner.example", "file://owner.example"),
    ],
)
def test_allowlist_cannot_enable_non_ingestion_operations(tmp_path, endpoint):
    with pytest.raises(ValueError):
        configuration(config(tmp_path, endpoint=endpoint, allowed_endpoints=[endpoint]))


def test_endpoint_requires_exact_local_allowlist(tmp_path):
    with pytest.raises(ValueError, match="not_allowlisted"):
        configuration(config(tmp_path, allowed_endpoints=["https://owner.example"]))


def test_configuration_permissions(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config(tmp_path)))
    path.chmod(0o644)
    with pytest.raises(ValueError, match="must_be_private"):
        load_configuration(path)
    path.chmod(0o600)
    assert load_configuration(path)["endpoint"] == ENDPOINT


def test_real_http_transport_does_not_follow_redirects(tmp_path):
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            paths.append(self.path)
            self.send_response(307)
            self.send_header("Location", "/device-command")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = ENDPOINT.replace(
        "https://owner.example", f"http://127.0.0.1:{server.server_port}"
    )
    collector = Collector(
        config(tmp_path, endpoint=endpoint, allowed_endpoints=[endpoint])
    )
    try:
        collector.enqueue(observation())
        assert collector.flush_once()["state"] == "blocked"
        assert paths == [endpoint.split(f":{server.server_port}", 1)[1]]
    finally:
        collector.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_batch_limits_and_invalid_ack_do_not_drop_samples(tmp_path):
    sent = []

    def send(endpoint, token, body):
        sent.append(body)
        return 201, b'{"accepted":100,"duplicates":0}'

    collector = Collector(config(tmp_path), send=send)
    try:
        for _ in range(101):
            collector.enqueue(observation())
        assert collector.flush_once()["state"] == "retry"
        assert len(json.loads(sent[0])["samples"]) <= 100
        assert len(sent[0]) <= 65536
        assert collector.db.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 101
    finally:
        collector.close()


def test_slow_forwarding_allows_enqueue_but_serializes_other_flushers(tmp_path):
    started, release = Event(), Event()
    result = []
    errors = []

    def send(*args):
        started.set()
        assert release.wait(10)
        return 201, b'{"schema_version":"telemetry/1","accepted":1,"duplicates":0}'

    def flush():
        worker = Collector(config(tmp_path), send=send)
        try:
            result.append(worker.flush_once())
        except Exception as exc:
            errors.append(exc)
        finally:
            worker.close()

    collector = Collector(
        config(tmp_path), send=lambda *args: pytest.fail("concurrent flush")
    )
    collector.enqueue(observation())
    thread = Thread(target=flush)
    thread.start()
    try:
        assert started.wait(3)
        collector.db.execute("PRAGMA busy_timeout=100")
        assert collector.enqueue(observation()) == 2
        assert collector.flush_once()["state"] == "busy"
    finally:
        release.set()
        thread.join(timeout=5)
        collector.close()
    assert not errors
    assert result[0]["count"] == 1
    collector = Collector(config(tmp_path))
    try:
        assert collector.db.execute("SELECT sequence FROM samples").fetchall() == [(2,)]
    finally:
        collector.close()


def test_observation_age_cannot_block_newer_samples(tmp_path):
    now = [200000.0]
    sent = []

    def send(endpoint, token, body):
        sent.extend(json.loads(body)["samples"])
        return 201, b'{"schema_version":"telemetry/1","accepted":1,"duplicates":0}'

    collector = Collector(config(tmp_path), send=send, clock=lambda: now[0])

    def observed(age):
        return observation(
            observed_at=datetime.fromtimestamp(now[0] - age, timezone.utc).isoformat()
        )

    try:
        with pytest.raises(ValueError, match="observation_outside_window"):
            collector.enqueue(observed(3 * 86400))
        collector.enqueue(observed(86400 - 60))
        now[0] += 45
        collector.enqueue(observed(1))
        assert collector.flush_once()["state"] == "sent"
        assert len(sent) == 1 and sent[0]["sequence"] == 2
    finally:
        collector.close()
