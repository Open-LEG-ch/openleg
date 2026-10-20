# SPDX-License-Identifier: AGPL-3.0-or-later
"""Experimental Solar API v1 reads. Hardware compatibility is unverified."""

import argparse
import ipaddress
import json
import sqlite3
import sys
import time
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from http.client import HTTPConnection, HTTPException
from socket import SHUT_RDWR
from threading import Lock, Timer
from urllib.error import URLError
from urllib.parse import urlsplit

import telemetry

MAX_RESPONSE_BYTES = 65536
REQUEST_BUDGET_SECONDS = 10
SOCKET_TIMEOUT_SECONDS = 5
LAN_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("invalid_fronius_response")
        result[key] = value
    return result


def decimal_text(value):
    """Preserve exact source decimals in local diagnostic JSON."""
    if isinstance(value, Decimal) and value.is_finite():
        return str(value)
    raise ValueError("invalid_fronius_response")


def _get(url):
    deadline = time.monotonic() + REQUEST_BUDGET_SECONDS
    parsed = urlsplit(url)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("invalid_fronius_address")
    connection = HTTPConnection(
        parsed.hostname,
        parsed.port,
        timeout=min(SOCKET_TIMEOUT_SECONDS, REQUEST_BUDGET_SECONDS),
    )
    watchdog = None
    try:
        connection.connect()
        sock = connection.sock

        def expire():
            try:
                sock.shutdown(SHUT_RDWR)
            except OSError:
                pass

        # Closing the socket also interrupts a header or chunked-body trickle.
        watchdog = Timer(max(0, deadline - time.monotonic()), expire)
        watchdog.daemon = True
        watchdog.start()
        target = parsed.path + ("?" + parsed.query if parsed.query else "")
        connection.request("GET", target, headers={"Accept": "application/json"})
        with connection.getresponse() as reply:
            if (
                reply.status != 200
                or reply.headers.get("Content-Encoding", "identity") != "identity"
            ):
                raise ValueError("invalid_fronius_response")
            raw = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError()
                chunk = reply.read1(min(4096, MAX_RESPONSE_BYTES + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ValueError("fronius_response_too_large")
        payload = json.loads(raw, object_pairs_hook=_unique_object, parse_float=Decimal)
        json.dumps(payload, allow_nan=False, default=decimal_text)
        if time.monotonic() >= deadline:
            raise TimeoutError()
        return payload
    finally:
        if watchdog is not None:
            watchdog.cancel()
        connection.close()


class FroniusReader:
    """One serialized reader per enrolled logger, with no discovery or controls."""

    def __init__(
        self,
        address,
        device_id,
        *,
        pv_only=False,
        cadence_seconds=30,
        get=None,
        clock=time.monotonic,
    ):
        try:
            if not isinstance(address, str):
                raise TypeError()
            parsed = ipaddress.IPv4Address(address)
            if not any(parsed in network for network in LAN_NETWORKS):
                raise ValueError()
        except (ValueError, TypeError, ipaddress.AddressValueError):
            raise ValueError("invalid_fronius_address") from None
        if (
            type(device_id) is not int
            or not 0 <= device_id <= 99
            or type(pv_only) is not bool
            or type(cadence_seconds) is not int
            or not 4 <= cadence_seconds <= 86400
        ):
            raise ValueError("invalid_fronius_profile")
        self.address = str(parsed)
        self.device_id = device_id
        self.pv_only = pv_only
        self.cadence_seconds = cadence_seconds
        self.get = get or _get
        self.clock = clock
        self.next_due = 0.0
        self.failures = 0
        self.api_confirmed = False
        self.lock = Lock()

    def poll(self):
        with self.lock:
            now = self.clock()
            if now < self.next_due:
                return {
                    "state": "backoff",
                    "retry_after": self.next_due - now,
                    "samples": [],
                }
            path = (
                "/solar_api/v1/GetInverterRealtimeData.cgi?Scope=Device&DeviceId="
                f"{self.device_id}&DataCollection=CommonInverterData"
                if self.api_confirmed
                else "/solar_api/GetAPIVersion.cgi"
            )
            try:
                payload = self.get(f"http://{self.address}{path}")
                if not self.api_confirmed:
                    if (
                        not isinstance(payload, dict)
                        or type(payload.get("APIVersion")) is not int
                        or payload["APIVersion"] != 1
                    ):
                        raise ValueError("unsupported_fronius_api")
                    self.api_confirmed = True
                    self.next_due = self.clock() + 4
                    result = {"state": "api_confirmed", "samples": [], "api_version": 1}
                else:
                    result = decode_common(
                        payload, device_id=self.device_id, pv_only=self.pv_only
                    )
                    self.next_due = self.clock() + self.cadence_seconds
                self.failures = 0
            except (
                ValueError,
                TypeError,
                ArithmeticError,
                RecursionError,
                URLError,
                OSError,
                HTTPException,
            ):
                self.failures = min(self.failures + 1, 5)
                self.next_due = self.clock() + min(300, 30 * 2 ** (self.failures - 1))
                result = {"state": "unavailable", "samples": []}
            return {**result, "retry_after": max(0, self.next_due - self.clock())}


def decode_common(payload, *, device_id, pv_only=False):
    """Keep logger response time separate from unavailable measurement time.

    `pv_only` is an explicit operator topology declaration. Without it, AC
    power must not be labelled PV generation: it could include battery flows.
    """
    try:
        if (
            type(device_id) is not int
            or not 0 <= device_id <= 99
            or type(pv_only) is not bool
        ):
            raise ValueError()
        head = payload["Head"]
        data = payload["Body"]["Data"]
        arguments = head["RequestArguments"]
        if (
            not isinstance(data, dict)
            or type(head["Status"]["Code"]) is not int
            or head["Status"]["Code"] != 0
            or any(
                arguments.get(key) != expected
                for key, expected in {
                    "DeviceId": str(device_id),
                    "DeviceClass": "Inverter",
                    "Scope": "Device",
                    "DataCollection": "CommonInverterData",
                }.items()
            )
        ):
            raise ValueError()
        responded_at = telemetry.timestamp(head["Timestamp"]).isoformat()
        status = data.get("DeviceStatus", {})
        if not isinstance(status, dict) or (
            "ErrorCode" in status and type(status["ErrorCode"]) is not int
        ):
            raise ValueError()
        quantities = {}
        for key, unit in (("PAC", "W"), ("TOTAL_ENERGY", "Wh")):
            if key not in data:
                continue
            channel = data[key]
            if channel["Unit"] != unit or type(channel["Value"]) not in (
                int,
                float,
                Decimal,
            ):
                raise ValueError()
            value = Decimal(str(channel["Value"]))
            if (
                not value.is_finite()
                or abs(value) > 10**12
                or (key == "TOTAL_ENERGY" and value < 0)
            ):
                raise ValueError()
            quantities[key] = value
    except (KeyError, TypeError, AttributeError, ValueError, InvalidOperation):
        raise ValueError("invalid_fronius_response") from None
    device_fault = bool(status.get("ErrorCode", 0))
    omitted = {
        key: "unsupported_precision"
        for key, value in quantities.items()
        if value.as_tuple().exponent < -9
    }
    samples = []
    for key, metric, unit in (
        ("PAC", "power", "W"),
        ("TOTAL_ENERGY", "energy_counter", "Wh"),
    ):
        if (
            pv_only
            and not device_fault
            and key in quantities
            and key not in omitted
            and quantities[key] >= 0
        ):
            samples.append(
                {
                    "metric": metric,
                    "unit": unit,
                    "value": format(quantities[key], "f"),
                    "direction": "generation",
                    "measurement_location": "pv_inverter",
                    "quality": "measured",
                    "observed_at": None,
                }
            )
    return {
        "state": "device_fault"
        if device_fault
        else "partial"
        if "PAC" not in data or omitted
        else "available",
        "responded_at": responded_at,
        "source_data": deepcopy(data),
        "samples": samples,
        "omitted_channels": omitted,
    }


def main():
    """Decode stdin offline; optional forwarding uses the existing private spool."""
    from telemetry_collector import Collector, load_configuration

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-id", required=True, type=int)
    parser.add_argument("--pv-only", action="store_true")
    parser.add_argument("--collector-config")
    parser.add_argument("--source-diagnostics", action="store_true")
    args = parser.parse_args()
    queued = 0
    collector = None
    try:
        raw = sys.stdin.buffer.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("fronius_response_too_large")
        payload = json.loads(raw, parse_float=Decimal, object_pairs_hook=_unique_object)
        json.dumps(payload, allow_nan=False, default=decimal_text)
        result = decode_common(payload, device_id=args.device_id, pv_only=args.pv_only)
        if args.collector_config:
            collector = Collector(load_configuration(args.collector_config))
            for sample in result["samples"]:
                collector.enqueue(sample)
                queued += 1
        if not args.source_diagnostics:
            del result["source_data"]
        print(
            json.dumps(
                {**result, "queued_count": queued},
                default=decimal_text,
                allow_nan=False,
            )
        )
        return 0
    except (
        ValueError,
        TypeError,
        ArithmeticError,
        RecursionError,
        OSError,
        sqlite3.Error,
    ):
        print(json.dumps({"state": "unavailable", "queued_count": queued}))
        return 2
    finally:
        if collector is not None:
            collector.close()


if __name__ == "__main__":
    sys.exit(main())
