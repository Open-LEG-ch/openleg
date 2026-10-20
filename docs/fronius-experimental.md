# Experimental Fronius Solar API reader (#615)

No model, logger or firmware has been validated on hardware. #615 and #621
remain open. This implementation provides synthetic-test coverage and a bounded
read client for a future authorized trial.

## Protocol boundary

`telemetry_fronius.FroniusReader` accepts one explicitly enrolled RFC1918 IPv4
literal and a Solar Net device ID from 0 to 99. It makes only these HTTP GETs:

- `/solar_api/GetAPIVersion.cgi`, requiring API version 1.
- `/solar_api/v1/GetInverterRealtimeData.cgi` with fixed `Scope=Device` and
  `DataCollection=CommonInverterData` parameters and the enrolled device ID.

It ignores the returned `BaseURL`. Hostnames, loopback, link-local, metadata,
public and shared-address destinations are rejected. Redirects and environment
proxies are disabled. No management or Modbus operation is implemented. This
experimental profile uses HTTP; installations requiring HTTPS need a separately
verified profile.

One reader serializes requests. It waits at least four seconds after version
confirmation; subsequent reads follow the configured cadence (30 seconds by
default, at least four). Failures back off from 30 to 300 seconds. Responses are
limited to 64 KiB. A socket watchdog applies a ten-second overall request
budget across connection, status, headers and body. Socket inactivity also times
out after at most five seconds. Malformed HTTP framing follows the same
unavailable/backoff path. Use one polling process per logger. Poll state is in memory; the
operator must preserve request spacing across restarts.

## Measurements and faults

`Head.Timestamp` is the logger response time, not the measurement time.
Normalized samples keep `observed_at=null`; reconnecting or forwarding them
cannot establish freshness. The existing owner view treats them as unknown age.
A 30-second end-to-end target has not been measured.

`PAC` is signed AC power in watts. Negative power stays in private source data
and is not emitted as PV generation. The caller must explicitly declare a
PV-only nonhybrid topology before any generation sample is emitted. Hybrid or
unknown topology does not justify that label. No grid export, battery flow or
site consumption is inferred.

`TOTAL_ENERGY` stays a cumulative Wh counter. A reset is not converted to
negative interval energy. Numeric precision is preserved with Decimal parsing;
a channel beyond the telemetry contract's nine decimal places is omitted with
a diagnostic while its exact source value remains available privately. Missing
power is not zero. Device error codes remain distinct from unavailable data.
Unknown source fields are retained locally and omitted from CLI output unless
`--source-diagnostics` is explicitly selected. Diagnostic decimals serialize as
strings to preserve precision.

## Offline use

Decode a private response file without contacting an installation:

```bash
python telemetry_fronius.py --device-id 1 --pv-only < private-response.json
```

Add `--collector-config /absolute/private/collector.json` to enqueue normalized
samples through the existing collector. The configuration must be owned by the
operator and have mode 0600. This command never flushes the queue or polls a
device. Flush separately through `telemetry_collector.py`. Enqueue is per
observation; an error reports how many were queued before it occurred.

## Authorized trial evidence still needed

Record owner consent, exact model, logger, firmware, API/compatibility version,
activation procedure, topology and the source's actual measurement-age bound.
The local API may expose installation information without authentication, so
restrict network access to the collector before enabling it. Local monitoring
does not establish that vendor cloud access is disabled.

Measure cadence, latency, night behavior, disconnection, recovery and optional
meter availability on that installation. Keep raw customer responses private;
publish only authorized, sanitized fixtures and compatibility facts. No trial,
API activation or support claim is recorded here.

Source: [Fronius Solar API V1 manual](https://www.fronius.com/~/downloads/Solar%20Energy/Operating%20Instructions/42%2C0410%2C2012.pdf),
revision 021 dated 15 May 2025, sections 2.4 to 2.6 and 4.1.
