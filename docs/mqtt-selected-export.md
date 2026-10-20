# Selected MQTT telemetry export (#616)

This local subscriber accepts explicitly selected sensor exports. No Swiss meter
reader has been validated. A real mapping, transformer factor and hardware
latency still require an authorized installation under #621.

## Select the source

Enroll one installation using the [telemetry contract](telemetry.md), with a
device ID, source ID and actual sampling cadence. Use a separate private
collector configuration and spool for each installation. The subscriber queues
observations; run the existing collector's `flush` command separately to send
them to the owner's chosen OpenLEG host. No Home Assistant service token is
accepted or sent to OpenLEG.

Each exact topic belongs to that enrollment:

```text
openleg/telemetry/v1/c3fc9e27-60af-460b-8b83-ec81d533dcb9/grid-import
```

Wildcards, discovery topics, command topics and other installation IDs are
rejected. Bind each topic to one canonical metric, unit, direction and location.
Overlapping channels are rejected. Supported metrics are power in W, cumulative
energy in Wh, and battery state of charge in percent. Export normalized
nonnegative magnitudes with an explicit direction. Do not guess a signed
meter convention, unit multiplier or transformer factor.

The versioned payload is:

```json
{
  "schema_version": "mqtt-telemetry/1",
  "sample_id": "source-reading-0001",
  "observed_at": null,
  "quality": "unknown",
  "value": "1234.56789"
}
```

Use a stable ID for retries and a new ID for a new source observation. Values
are decimal strings. The enrolled channel supplies the unit and direction;
payloads cannot change them. Unknown fields and duplicate JSON keys are rejected.
Payloads are limited to 8 KiB.

Set `observed_at` only from a documented measurement timestamp with a timezone.
Broker receipt, Home Assistant state updates and reconnect time do not establish
measurement time. Leave it null when unknown. Preserve measured, estimated or
unknown source quality. Retained messages follow the same rule: replaying an old
value cannot refresh it. Cumulative energy remains a counter across resets;
the subscriber never converts it to interval consumption or billing evidence.

## Broker access

Use a dedicated subscriber account with read permission on the selected exact
topics and no publish permission. Keep its credentials in an operator-owned
0600 file. The example below is a template; substitute local credentials in that
private file.

```json
{
  "broker": {
    "host": "192.168.1.2",
    "port": 8883,
    "username": "openleg-selected-reader",
    "password": "REPLACE_IN_PRIVATE_FILE",
    "ca_file": "/absolute/private/broker-ca.pem",
    "allow_loopback_plaintext": false
  },
  "topics": {
    "openleg/telemetry/v1/c3fc9e27-60af-460b-8b83-ec81d533dcb9/grid-import": {
      "metric": "energy_counter",
      "unit": "Wh",
      "direction": "import",
      "measurement_location": "grid_connection"
    }
  }
}
```

The broker address must be a literal RFC1918 or loopback IPv4 address. Network
connections require TLS with certificate and hostname/IP verification. The
certificate must cover that address. A custom trusted CA is optional. Plaintext
requires both a literal loopback address and the explicit loopback option; it
is only for an isolated local broker. Inherited MQTT and PySocks proxies are
disabled. No internet port forwarding is needed.

For Mosquitto, the dedicated reader's ACL contains only its selected topics:

```text
user openleg-selected-reader
topic read openleg/telemetry/v1/c3fc9e27-60af-460b-8b83-ec81d533dcb9/grid-import
```

Disable anonymous access and set the broker's `max_packet_size` to 16384. The
subscriber also advertises that MQTT 5 limit and bounds payload processing.
Use a trusted local broker that enforces packet, queue and access limits. The
subscriber makes no publish requests and does not perform discovery.

## Home Assistant example

Select a sensor whose value is already a cumulative import counter in Wh. This
automation runs inside Home Assistant and publishes through its configured MQTT
integration. Its broker account needs permission to write this export topic;
OpenLEG's dedicated reader account still has read permission only. Review the
broker's existing ACLs before applying a restriction to Home Assistant's account.

```yaml
alias: Export selected import counter to OpenLEG
triggers:
  - trigger: state
    entity_id: sensor.selected_grid_import_wh
conditions:
  - condition: template
    value_template: "{{ trigger.to_state is not none and is_number(trigger.to_state.state) }}"
actions:
  - action: mqtt.publish
    data:
      topic: openleg/telemetry/v1/c3fc9e27-60af-460b-8b83-ec81d533dcb9/grid-import
      qos: 1
      retain: true
      payload: >-
        {{ {
          'schema_version': 'mqtt-telemetry/1',
          'sample_id': trigger.to_state.last_reported.isoformat().replace('+', '_'),
          'observed_at': none,
          'quality': 'unknown',
          'value': trigger.to_state.state
        } | to_json }}
```

`last_reported` identifies the export, not the measurement time. This example
therefore keeps age and quality unknown. A verified reader-specific mapping can
use its actual measurement timestamp and quality. The example does not validate
any reader, sensor name or transformer factor.

## Run and recover

Install the optional local dependency and start one subscriber per enrollment:

```sh
python -m pip install -r requirements-telemetry.txt
python telemetry_mqtt.py /absolute/private/collector.json /absolute/private/mqtt.json
```

Publish with QoS 1. The subscriber acknowledges after the normalized sample and
its duplicate marker commit together in the private SQLite spool. Reconnects
use the same client ID and a one-day broker session, with retry delays from two
to 300 seconds. The broker must retain pending QoS 1 messages for that session.
Storage failure or a full queue disconnects without acknowledging that sample.
The process prints a fixed failure state without credentials or payloads and exits
unsuccessfully so an operator or supervisor can detect the stop. Restart after
resolving the cause. Invalid messages are discarded and
acknowledged so they cannot block later readings.

Duplicate identity is retained for at most one day, including after a successful
flush. History holds the 10,000 most recently accepted source IDs; higher traffic
evicts older IDs sooner. An identical retry within that history returns its
original sequence; changing a payload under the same ID fails. A retry outside
the history can queue again with a new sequence and its original source timestamp.
IDs must therefore remain unique at the source. Expiry and eviction never refresh
observation time or upgrade unknown freshness. The separate pending queue holds
at most 10,000 samples and stops ingestion when full.

Sources: [Home Assistant MQTT](https://www.home-assistant.io/integrations/mqtt/),
[Paho Python client](https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html),
[Mosquitto configuration](https://mosquitto.org/man/mosquitto-conf-5.html).
