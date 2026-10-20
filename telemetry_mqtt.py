# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local selected-topic MQTT export. No discovery, publication or device controls."""

import argparse
import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import ssl
import stat
from datetime import UTC, datetime
from pathlib import Path

import telemetry
from telemetry_collector import Collector, load_configuration

MAX_PAYLOAD = 8192
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def configuration(payload, collector_config):
    if not isinstance(payload, dict) or set(payload) != {"broker", "topics"}:
        raise ValueError("invalid_mqtt_configuration")
    broker = payload["broker"]
    required = {
        "host",
        "port",
        "username",
        "password",
        "ca_file",
        "allow_loopback_plaintext",
    }
    if not isinstance(broker, dict) or set(broker) != required:
        raise ValueError("invalid_broker_configuration")
    if not isinstance(broker["host"], str):
        raise TypeError("local_broker_ip_required")
    try:
        address = ipaddress.IPv4Address(broker["host"])
    except (ValueError, TypeError):
        raise ValueError("local_broker_ip_required") from None
    if not address.is_loopback and not any(
        address in network for network in PRIVATE_NETWORKS
    ):
        raise ValueError("local_broker_ip_required")
    telemetry.integer(broker["port"], 1, 65535)
    if type(broker["allow_loopback_plaintext"]) is not bool:
        raise ValueError("invalid_loopback_option")
    if broker["allow_loopback_plaintext"] and not address.is_loopback:
        raise ValueError("plaintext_requires_loopback")
    for key in ("username", "password"):
        if (
            not isinstance(broker[key], str)
            or not 1 <= len(broker[key]) <= 256
            or "\x00" in broker[key]
        ):
            raise ValueError("dedicated_broker_credentials_required")
    if broker["ca_file"] is not None and (
        not isinstance(broker["ca_file"], str)
        or not Path(broker["ca_file"]).is_absolute()
    ):
        raise ValueError("absolute_ca_path_required")
    topics = payload["topics"]
    if not isinstance(topics, dict) or not 1 <= len(topics) <= 20:
        raise ValueError("selected_topics_required")
    prefix = "openleg/telemetry/v1/" + collector_config["installation_id"] + "/"
    normalized = {}
    channels = set()
    for topic, mapping in topics.items():
        if (
            not isinstance(topic, str)
            or not topic.startswith(prefix)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", topic[len(prefix) :])
            or topic[len(prefix) :].lower()
            in {"set", "cmd", "command", "commands", "config", "discovery"}
        ):
            raise ValueError("invalid_selected_topic")
        if not isinstance(mapping, dict) or set(mapping) != {
            "metric",
            "unit",
            "direction",
            "measurement_location",
        }:
            raise ValueError("invalid_channel_mapping")
        if not isinstance(mapping["metric"], str) or mapping["metric"] not in {
            "power",
            "energy_counter",
            "state_of_charge",
        }:
            raise ValueError("unsupported_mqtt_metric")
        telemetry.observation(
            {
                **mapping,
                **{
                    k: collector_config["source"][k]
                    for k in ("device_id", "source_id", "cadence_seconds")
                },
                "sample_id": "configuration-check",
                "sequence": 1,
                "value": "0",
                "observed_at": None,
                "quality": "unknown",
            },
            {**collector_config["source"], "raw_retention_days": 1},
            datetime.now(UTC),
        )
        channel = (
            mapping["metric"],
            mapping["direction"],
            mapping["measurement_location"],
        )
        if channel in channels:
            raise ValueError("overlapping_selected_channels")
        channels.add(channel)
        normalized[topic] = dict(mapping)
    return {"broker": dict(broker), "topics": normalized}


def load_mqtt_configuration(path, collector_config):
    with open(path, encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077
            or info.st_uid != os.getuid()
        ):
            raise ValueError("mqtt_configuration_must_be_private")
        raw = handle.read(telemetry.MAX_BYTES + 1)
        if len(raw.encode()) > telemetry.MAX_BYTES:
            raise ValueError("mqtt_configuration_too_large")
    return configuration(
        json.loads(raw, object_pairs_hook=_unique_object), collector_config
    )


class MQTTExport:
    def __init__(self, collector, settings):
        self.collector = collector
        self.settings = configuration(settings, collector.config)

    def receive(self, topic, payload, *, retained):
        if topic not in self.settings["topics"]:
            raise ValueError("unselected_topic")
        if not isinstance(payload, bytes) or len(payload) > MAX_PAYLOAD:
            raise ValueError("mqtt_payload_too_large")
        if type(retained) is not bool:
            raise ValueError("invalid_retained_flag")
        try:
            data = json.loads(payload, object_pairs_hook=_unique_object)
        except RecursionError:
            raise ValueError("mqtt_json_too_deep") from None
        if (
            not isinstance(data, dict)
            or set(data)
            != {"schema_version", "sample_id", "observed_at", "quality", "value"}
            or data["schema_version"] != "mqtt-telemetry/1"
            or not isinstance(data["value"], str)
        ):
            raise ValueError("invalid_mqtt_sample")
        telemetry.identifier(data["sample_id"])
        source_key = hashlib.sha256(
            (topic + "\x00" + data["sample_id"]).encode()
        ).hexdigest()
        return self.collector.enqueue(
            {
                **self.settings["topics"][topic],
                **{k: data[k] for k in ("value", "quality", "observed_at")},
            },
            source_key=source_key,
        )


def subscriber(export):
    # Optional local collector dependency; the web application does not import it.
    import paho.mqtt.client as mqtt
    from paho.mqtt.packettypes import PacketTypes
    from paho.mqtt.properties import Properties

    class DirectClient(mqtt.Client):
        stop_reason: str | None = None

        def _get_proxy(self):
            # Paho 2.1.0 otherwise inherits mqtt_proxy and PySocks defaults.
            return None

    broker = export.settings["broker"]
    client = DirectClient(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="openleg-" + export.collector.config["installation_id"],
        protocol=mqtt.MQTTv5,
        manual_ack=True,
    )
    client.username_pw_set(broker["username"], broker["password"])
    if not broker["allow_loopback_plaintext"]:
        context = ssl.create_default_context(cafile=broker["ca_file"])
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        client.tls_set_context(context)
    client.reconnect_delay_set(min_delay=2, max_delay=300)

    def on_connect(connection, userdata, flags, reason_code, properties):
        if reason_code == 0:
            connection.subscribe([(topic, 1) for topic in export.settings["topics"]])

    def on_message(connection, userdata, message):
        try:
            export.receive(
                message.topic, bytes(message.payload), retained=bool(message.retain)
            )
        except ValueError as exc:
            if str(exc) == "collector_queue_full":
                connection.stop_reason = "collector_queue_full"
                connection.disconnect()
                return
            print(json.dumps({"state": "rejected_sample"}), flush=True)
        except sqlite3.Error:
            connection.stop_reason = "storage_unavailable"
            connection.disconnect()
            return
        connection.ack(message.mid, message.qos)

    client.on_connect = on_connect
    client.on_message = on_message
    properties = Properties(PacketTypes.CONNECT)
    properties.SessionExpiryInterval = 86400
    properties.MaximumPacketSize = 16384
    properties.ReceiveMaximum = 20
    client.connect_async(
        broker["host"],
        broker["port"],
        keepalive=30,
        clean_start=False,
        properties=properties,
    )
    return client


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("collector_config")
    parser.add_argument("mqtt_config")
    args = parser.parse_args()
    collector = None
    try:
        config = load_configuration(args.collector_config)
        settings = load_mqtt_configuration(args.mqtt_config, config)
        collector = Collector(config)
        client = subscriber(MQTTExport(collector, settings))
        try:
            client.loop_forever(retry_first_connection=True)
            if client.stop_reason is not None:
                print(json.dumps({"state": client.stop_reason}), flush=True)
                return 1
        finally:
            client.disconnect()
    except KeyboardInterrupt:
        return 0
    except (ValueError, TypeError, OSError, sqlite3.Error, ImportError, RecursionError):
        print(json.dumps({"state": "mqtt_unavailable"}))
        return 1
    finally:
        if collector is not None:
            collector.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
