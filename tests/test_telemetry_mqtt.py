# SPDX-License-Identifier: AGPL-3.0-or-later
"""Selected-topic exports preserve source age and durable duplicate identity."""

import json
import sqlite3
import ssl
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest
import yaml
from jinja2 import Environment

import telemetry_mqtt as mqtt_module
from telemetry_collector import Collector
from telemetry_mqtt import (
    MQTTExport,
    configuration,
    load_mqtt_configuration,
    subscriber,
)

INSTALLATION = "c3fc9e27-60af-460b-8b83-ec81d533dcb9"
TOPIC = f"openleg/telemetry/v1/{INSTALLATION}/grid-import"
NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)


def collector_config(tmp_path):
    return {
        "installation_id": INSTALLATION,
        "source": {
            "name": "Selected meter",
            "device_id": "meter",
            "source_id": "selected-mqtt",
            "cadence_seconds": 30,
        },
        "spool": str(tmp_path / "private" / "queue.sqlite3"),
        "endpoint": None,
        "allowed_endpoints": [],
    }


def settings():
    return {
        "broker": {
            "host": "192.168.1.2",
            "port": 8883,
            "username": "openleg-selected-reader",
            "password": "synthetic-local-broker-password",
            "ca_file": None,
            "allow_loopback_plaintext": False,
        },
        "topics": {
            TOPIC: {
                "metric": "energy_counter",
                "unit": "Wh",
                "direction": "import",
                "measurement_location": "grid_connection",
            }
        },
    }


def payload(**overrides):
    return json.dumps(
        {
            "schema_version": "mqtt-telemetry/1",
            "sample_id": "source-reading-1",
            "observed_at": NOW.isoformat(),
            "quality": "measured",
            "value": "1234.56789",
            **overrides,
        }
    ).encode()


@pytest.fixture
def export(tmp_path):
    queue = Collector(collector_config(tmp_path), clock=lambda: NOW.timestamp())
    bridge = MQTTExport(queue, settings())
    yield bridge
    queue.close()


@pytest.mark.parametrize(
    "topic",
    [
        "#",
        "openleg/telemetry/v1/+/grid-import",
        "homeassistant/sensor/grid/config",
        TOPIC + "/set",
        TOPIC.replace(INSTALLATION, "00000000-0000-0000-0000-000000000000"),
        TOPIC.replace("grid-import", "command"),
    ],
)
def test_configuration_rejects_wildcards_discovery_commands_and_other_installations(
    tmp_path, topic
):
    config = settings()
    config["topics"] = {topic: config["topics"][TOPIC]}
    with pytest.raises(ValueError):
        configuration(config, collector_config(tmp_path))


@pytest.mark.parametrize(
    "host", ["broker.example", "169.254.169.254", "8.8.8.8", "100.64.1.1"]
)
def test_broker_destination_is_an_explicit_local_ip(tmp_path, host):
    config = settings()
    config["broker"]["host"] = host
    with pytest.raises(ValueError):
        configuration(config, collector_config(tmp_path))


def test_plaintext_requires_explicit_literal_loopback(tmp_path):
    config = settings()
    config["broker"]["allow_loopback_plaintext"] = True
    with pytest.raises(ValueError, match="loopback"):
        configuration(config, collector_config(tmp_path))
    config["broker"]["host"] = "127.0.0.1"
    assert configuration(config, collector_config(tmp_path))["broker"][
        "allow_loopback_plaintext"
    ]


def test_deduplication_survives_restart_and_changed_replay_is_rejected(export):
    assert export.receive(TOPIC, payload(), retained=False) == 1
    config = export.collector.config
    export.collector.close()
    export.collector = Collector(config, clock=lambda: NOW.timestamp())
    assert export.receive(TOPIC, payload(), retained=True) == 1
    with pytest.raises(ValueError, match="source_sample_conflict"):
        export.receive(TOPIC, payload(value="1234.56788"), retained=True)
    rows = export.collector.db.execute("SELECT body FROM samples").fetchall()
    assert len(rows) == 1
    sample = json.loads(rows[0][0])
    assert sample["value"] == "1234.56789"
    assert sample["observed_at"] == NOW.isoformat()
    assert sample["direction"] == "import"


def test_retained_old_reading_keeps_its_source_time_and_unknown_stays_unknown(export):
    old = NOW - timedelta(hours=12)
    export.receive(TOPIC, payload(observed_at=old.isoformat()), retained=True)
    export.receive(
        TOPIC, payload(sample_id="unknown-age", observed_at=None), retained=True
    )
    rows = [
        json.loads(row[0])
        for row in export.collector.db.execute(
            "SELECT body FROM samples ORDER BY sequence"
        )
    ]
    assert rows[0]["observed_at"] == old.isoformat()
    assert rows[1]["observed_at"] is None
    assert rows[1]["quality"] == "measured"


def test_counter_reset_is_a_counter_not_negative_interval_energy(export):
    export.receive(TOPIC, payload(value="1000"), retained=False)
    export.receive(TOPIC, payload(sample_id="reset", value="1"), retained=False)
    rows = [
        json.loads(row[0])
        for row in export.collector.db.execute(
            "SELECT body FROM samples ORDER BY sequence"
        )
    ]
    assert [(row["metric"], row["value"]) for row in rows] == [
        ("energy_counter", "1000"),
        ("energy_counter", "1"),
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": "mqtt-telemetry/2"},
        {"command": "switch-on"},
        {"unit": "kWh"},
        {"quality": "validated"},
        {"value": "NaN"},
        {"sample_id": ""},
        {"observed_at": "2026-10-20T12:00:00"},
        {"observed_at": (NOW + timedelta(minutes=6)).isoformat()},
        {"observed_at": (NOW - timedelta(days=2)).isoformat()},
    ],
)
def test_invalid_or_privilege_expanding_payloads_never_queue(export, changes):
    with pytest.raises(ValueError):
        export.receive(TOPIC, payload(**changes), retained=False)
    assert (
        export.collector.db.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 0
    )


def test_unselected_or_oversized_messages_never_queue(export):
    with pytest.raises(ValueError, match="unselected_topic"):
        export.receive(TOPIC + "/set", payload(), retained=False)
    with pytest.raises(ValueError, match="payload_too_large"):
        export.receive(TOPIC, b" " * 8193, retained=False)
    assert (
        export.collector.db.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 0
    )


def test_queue_failure_can_be_retried_without_losing_source_identity(
    export, monkeypatch
):
    import telemetry_collector

    monkeypatch.setattr(telemetry_collector, "MAX_QUEUE", 0)
    with pytest.raises(ValueError, match="queue_full"):
        export.receive(TOPIC, payload(), retained=False)
    monkeypatch.setattr(telemetry_collector, "MAX_QUEUE", 10000)
    assert export.receive(TOPIC, payload(), retained=False) == 1
    assert export.receive(TOPIC, payload(), retained=False) == 1


def test_duplicate_retry_works_even_when_the_queue_is_full(export, monkeypatch):
    import telemetry_collector

    export.receive(TOPIC, payload(), retained=False)
    before = export.collector.db.execute("SELECT * FROM samples").fetchall()
    monkeypatch.setattr(telemetry_collector, "MAX_QUEUE", 1)
    assert export.receive(TOPIC, payload(), retained=True) == 1
    assert export.collector.db.execute("SELECT * FROM samples").fetchall() == before


def test_failed_sample_insert_rolls_back_duplicate_marker_and_sequence(export):
    export.collector.db.execute(
        "CREATE TRIGGER fail_sample BEFORE INSERT ON samples BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END"
    )
    with pytest.raises(sqlite3.Error):
        export.receive(TOPIC, payload(), retained=False)
    assert (
        export.collector.db.execute("SELECT COUNT(*) FROM source_samples").fetchone()[0]
        == 0
    )
    assert export.collector.db.execute("SELECT sequence FROM state").fetchone()[0] == 0
    export.collector.db.execute("DROP TRIGGER fail_sample")
    assert export.receive(TOPIC, payload(), retained=False) == 1


def test_configuration_is_private_and_duplicate_keys_are_rejected(tmp_path):
    path = tmp_path / "mqtt.json"
    path.write_text(json.dumps(settings()))
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        load_mqtt_configuration(path, collector_config(tmp_path))
    path.chmod(0o600)
    assert (
        load_mqtt_configuration(path, collector_config(tmp_path))["topics"]
        == settings()["topics"]
    )
    path.write_text('{"broker":{},"broker":{},"topics":{}}')
    with pytest.raises(ValueError, match="duplicate_json_key"):
        load_mqtt_configuration(path, collector_config(tmp_path))


def test_duplicate_payload_keys_are_rejected(export):
    with pytest.raises(ValueError, match="duplicate_json_key"):
        export.receive(TOPIC, b'{"value":"1","value":"2"}', retained=False)


def test_deeply_nested_json_is_an_invalid_sample(export):
    with pytest.raises(ValueError):
        export.receive(TOPIC, b"[" * 4000 + b"0" + b"]" * 4000, retained=False)


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.options = kwargs
        self.subscriptions = []
        self.acknowledgements = []
        self.disconnected = False
        self.context = None

    def username_pw_set(self, *args):
        self.credentials = args

    def tls_set_context(self, context):
        self.context = context

    def reconnect_delay_set(self, **kwargs):
        self.backoff = kwargs

    def connect_async(self, *args, **kwargs):
        self.destination = args
        self.connection_options = kwargs

    def subscribe(self, topics):
        self.subscriptions.append(topics)

    def ack(self, mid, qos):
        self.acknowledgements.append((mid, qos))

    def disconnect(self):
        self.disconnected = True


@pytest.fixture
def client(export, monkeypatch):
    import paho.mqtt.client as mqtt

    monkeypatch.setattr(mqtt, "Client", FakeClient)
    return subscriber(export)


def test_subscriber_verifies_tls_and_only_subscribes_to_selected_topics(client):
    assert client.context.verify_mode == ssl.CERT_REQUIRED
    assert client.context.check_hostname is True
    assert client.context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert client.options["manual_ack"] is True
    assert client.backoff == {"min_delay": 2, "max_delay": 300}
    assert client.connection_options["clean_start"] is False
    properties = client.connection_options["properties"]
    assert properties.SessionExpiryInterval == 86400
    assert properties.MaximumPacketSize == 16384
    assert properties.ReceiveMaximum == 20
    client.on_connect(client, None, None, 0, None)
    client.on_connect(client, None, None, 0, None)
    assert client.subscriptions == [[(TOPIC, 1)], [(TOPIC, 1)]]


def test_subscriber_only_acknowledges_after_durable_enqueue(
    client, export, monkeypatch
):
    message = SimpleNamespace(
        topic=TOPIC, payload=payload(), retain=True, mid=17, qos=1
    )
    client.on_message(client, None, message)
    assert client.acknowledgements == [(17, 1)]
    assert (
        export.collector.db.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1
    )
    import telemetry_collector

    monkeypatch.setattr(telemetry_collector, "MAX_QUEUE", 1)
    message.payload = payload(sample_id="next")
    client.on_message(client, None, message)
    assert client.acknowledgements == [(17, 1)]
    assert client.disconnected


def test_subscriber_discards_invalid_messages_without_acknowledging_storage_failure(
    client, export
):
    message = SimpleNamespace(
        topic=TOPIC + "/set", payload=payload(), retain=False, mid=18, qos=1
    )
    client.on_message(client, None, message)
    assert client.acknowledgements == [(18, 1)]
    assert (
        export.collector.db.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 0
    )
    message.topic = TOPIC
    export.collector.db.close()
    client.on_message(client, None, message)
    assert client.acknowledgements == [(18, 1)]
    assert client.disconnected


def test_home_assistant_example_produces_valid_deduplicated_export(export):
    document = (
        Path(__file__).resolve().parents[1] / "docs/mqtt-selected-export.md"
    ).read_text()
    automation = yaml.safe_load(document.split("```yaml\n", 1)[1].split("```", 1)[0])
    action = automation["actions"][0]["data"]
    environment = Environment()
    environment.filters["to_json"] = json.dumps
    rendered = (
        environment.from_string(action["payload"])
        .render(
            trigger=SimpleNamespace(
                to_state=SimpleNamespace(last_reported=NOW, state="1234.56789")
            )
        )
        .encode()
    )
    assert action["qos"] == 1
    assert action["retain"] is True
    assert export.receive(action["topic"], rendered, retained=False) == 1
    assert export.receive(action["topic"], rendered, retained=True) == 1
    sample = json.loads(
        export.collector.db.execute("SELECT body FROM samples").fetchone()[0]
    )
    assert sample["observed_at"] is None
    assert sample["quality"] == "unknown"


def test_concurrent_duplicate_delivery_is_one_atomic_sample(export):
    config = export.collector.config
    barrier = Barrier(2)

    def deliver():
        queue = Collector(config, clock=lambda: NOW.timestamp())
        try:
            bridge = MQTTExport(queue, settings())
            barrier.wait(timeout=5)
            return bridge.receive(TOPIC, payload(), retained=False)
        finally:
            queue.close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        first, second = workers.submit(deliver), workers.submit(deliver)
        assert first.result(timeout=10) == second.result(timeout=10) == 1
    assert (
        export.collector.db.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1
    )
    assert (
        export.collector.db.execute("SELECT COUNT(*) FROM source_samples").fetchone()[0]
        == 1
    )


def test_duplicate_history_expires_on_flush_without_new_source_messages(export):
    export.receive(TOPIC, payload(observed_at=None), retained=False)
    export.collector.clock = lambda: NOW.timestamp() + 86401
    export.collector.flush_once()
    assert (
        export.collector.db.execute("SELECT COUNT(*) FROM source_samples").fetchone()[0]
        == 0
    )


@pytest.mark.parametrize("proxy_source", ["environment", "default"])
def test_subscriber_never_routes_loopback_credentials_through_inherited_proxies(
    export, monkeypatch, proxy_source
):
    import paho.mqtt.client as mqtt

    def forbidden_proxy(*args, **kwargs):
        pytest.fail("broker credentials routed through an inherited proxy")

    monkeypatch.setattr(
        mqtt,
        "socks",
        SimpleNamespace(
            HTTP=3,
            SOCKS4=1,
            SOCKS5=2,
            get_default_proxy=lambda: (3, "203.0.113.99", 8080, True, None, None),
            create_connection=forbidden_proxy,
        ),
    )
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("MQTT_PROXY", raising=False)
    if proxy_source == "environment":
        monkeypatch.setenv("mqtt_proxy", "http://203.0.113.99:8080")
    else:
        monkeypatch.delenv("mqtt_proxy", raising=False)
    export.settings["broker"].update(host="127.0.0.1", allow_loopback_plaintext=True)
    calls = []
    sentinel = object()

    def direct_connection(address, **kwargs):
        calls.append(address)
        return sentinel

    monkeypatch.setattr(mqtt.socket, "create_connection", direct_connection)
    client = subscriber(export)
    assert client._create_socket_connection() is sentinel
    assert calls == [("127.0.0.1", 8883)]


def test_busy_export_evicts_old_history_without_stopping_an_empty_queue(
    tmp_path, monkeypatch
):
    import telemetry_collector

    monkeypatch.setattr(telemetry_collector, "MAX_SOURCE_HISTORY", 2, raising=False)
    config = collector_config(tmp_path)
    endpoint = f"https://owner.example/api/telemetry/v1/communities/community-a/installations/{INSTALLATION}/samples"
    config.update(
        endpoint=endpoint, allowed_endpoints=[endpoint], credential="olt_" + "a" * 43
    )

    def send(endpoint, credential, body):
        return 201, json.dumps(
            {
                "schema_version": "telemetry/1",
                "accepted": len(json.loads(body)["samples"]),
                "duplicates": 0,
            }
        ).encode()

    queue = Collector(config, send=send, clock=lambda: NOW.timestamp())
    bridge = MQTTExport(queue, settings())
    try:
        for number in range(1, 4):
            assert (
                bridge.receive(
                    TOPIC, payload(sample_id=f"source-{number}"), retained=False
                )
                == number
            )
            assert queue.flush_once()["state"] == "sent"
        assert queue.db.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 0
        assert (
            queue.db.execute("SELECT COUNT(*) FROM source_samples").fetchone()[0] == 2
        )
        assert bridge.receive(TOPIC, payload(sample_id="source-3"), retained=True) == 3
        # A retry beyond the bounded history can queue again, with its old source age.
        assert bridge.receive(TOPIC, payload(sample_id="source-1"), retained=True) == 4
        sample = json.loads(queue.db.execute("SELECT body FROM samples").fetchone()[0])
        assert sample["observed_at"] == NOW.isoformat()
    finally:
        queue.close()


@pytest.mark.parametrize(
    "failure,expected",
    [("queue", "collector_queue_full"), ("storage", "storage_unavailable")],
)
def test_cli_exits_unsuccessfully_when_durable_ingestion_stops(
    tmp_path, monkeypatch, capsys, failure, expected
):
    import paho.mqtt.client as mqtt

    import telemetry_collector

    collector_path = tmp_path / "collector.json"
    mqtt_path = tmp_path / "mqtt.json"
    collector_path.write_text(json.dumps(collector_config(tmp_path)))
    mqtt_path.write_text(json.dumps(settings()))
    collector_path.chmod(0o600)
    mqtt_path.chmod(0o600)
    monkeypatch.setattr(
        "sys.argv", ["telemetry_mqtt.py", str(collector_path), str(mqtt_path)]
    )
    monkeypatch.setattr(mqtt, "Client", FakeClient)
    actual_subscriber = mqtt_module.subscriber

    def controlled_subscriber(export):
        client = actual_subscriber(export)

        def run(**kwargs):
            if failure == "queue":
                monkeypatch.setattr(telemetry_collector, "MAX_QUEUE", 0)
            else:
                export.collector.db.close()
            client.on_message(
                client,
                None,
                SimpleNamespace(
                    topic=TOPIC,
                    payload=payload(observed_at=None),
                    retain=False,
                    mid=19,
                    qos=1,
                ),
            )
            assert client.acknowledgements == []

        client.loop_forever = run
        return client

    monkeypatch.setattr(mqtt_module, "subscriber", controlled_subscriber)
    assert mqtt_module.main() == 1
    output = capsys.readouterr().out
    assert json.loads(output) == {"state": expected}
    assert settings()["broker"]["password"] not in output
