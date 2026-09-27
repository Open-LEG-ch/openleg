# Private operational telemetry (#614)

This API accepts owner-authorized installation observations. It does not read
home devices, accept their credentials, or feed billing. Validated VNB ingestion
and the operator API remain separate. Hardware adapters and live dashboard
screens belong to #615–617.

The three trust boundaries are device to local collector, collector to the
owner's chosen OpenLEG host, and host to an authorized viewer. A read-only
collector does not make a device physically incapable of control. Operators
must restrict device access locally. OpenLEG exposes no scanner, register
operation, device proxy, arbitrary forwarding URL or command channel.

## Enrollment and access

Use the existing authenticated dashboard session. Every management mutation
requires `X-CSRF-Token` containing that session's `dashboard_csrf_token`.
Do not put credentials in URLs, command arguments, access logs or tickets.

The base path is
`/api/telemetry/v1/communities/{community_id}/installations`.

| Method and suffix | Behavior |
|---|---|
| POST base | Enroll with `name`, `device_id`, `source_id`, `cadence_seconds`; optional retention settings below. Returns installation and a one-time `credential`. |
| GET base | List the owner's installations and sequence watermarks. |
| POST `/{id}/samples` | Ingestion with `Authorization: Bearer olt_…`; session not required. |
| GET `/{id}/samples` or `/{id}/export` | Private JSON samples, at most 1,000 ordered by sequence. Continue with `?after={next_after}` until an empty page. |
| GET `/{id}/aggregates` | Latest 1,000 hourly statistic groups, explicitly a bounded view. |
| POST `/{id}/revoke` | Disable ingestion; owner history remains accessible. |
| POST `/{id}/rotate` | Replace credential; returns new credential and persistent `highwater`. Old credential stops working. |
| POST `/{id}/share` or `/{id}/unshare` | JSON `{"viewer_building_id":"…"}`; explicit grant or revocation. |
| POST `/{id}/retention` | JSON with both `raw_retention_days` and `aggregate_retention_days`; shorter horizons clean immediately. |
| DELETE `/{id}` | Delete installation, samples, aggregates, credential and shares. |

Owners and viewers must remain verified, confirmed members of the same
community. Community administrators and neighbour consent have no implicit
access. A share permits samples, aggregates and exports; it never permits
management or ingestion. Revocation is checked on every subsequent request.
Removing a viewer's membership or changing its status away from confirmed
deletes their grants. Rejoining requires the owner to share again.
Previously downloaded copies cannot be recalled. Enrollment is capped at 20
installations per owner.

Credentials are hashed at rest and bind one installation and one community.
They cannot read telemetry or access the broader operator API. All telemetry
responses, including errors, have `Cache-Control: no-store` and
`Referrer-Policy: no-referrer`.

## Sample contract

POST JSON, no compression, at most 65,536 bytes and 100 samples:

```json
{
  "schema_version": "telemetry/1",
  "samples": [{
    "sample_id": "collector-00001",
    "sequence": 1,
    "device_id": "inverter-1",
    "source_id": "local-reader",
    "cadence_seconds": 30,
    "metric": "power",
    "unit": "W",
    "value": "123.5",
    "direction": "generation",
    "measurement_location": "pv_inverter",
    "quality": "measured",
    "observed_at": null
  }]
}
```

- `device_id`, `source_id` and cadence must match enrollment. Identifiers use
  ASCII letters, digits, `_`, `.`, `:`, `-`, at most 128 characters.
- Metrics are `power`/`W`, `energy_counter`/`Wh`, `interval_energy`/`Wh`,
  and `state_of_charge`/`%`. Counter resets are not inferred. Interval energy
  also requires timezone-aware `interval_start` and `interval_end`; end equals
  observation time, start precedes end, duration is at most one day.
- Values are finite nonnegative magnitudes, at most 10^12 with nine decimal
  places, or at most 100 for state of charge. The local adapter must normalize
  any signed device convention into explicit directions without netting them.
- Locations/directions: `grid_connection` uses `import`/`export`;
  `pv_inverter` uses `generation`; `site` uses `consumption`/`generation`;
  `battery` uses `charge`/`discharge`, or `stored` for state of charge.
- Quality is `measured`, `estimated` or `unknown`. Freshness does not upgrade
  quality or make a measurement validated for billing.
- Observation time is timezone-aware ISO 8601 or explicit `null`. Receipt is
  assigned by the server. Unknown observation/quality means unknown freshness;
  future observation means clock skew. Otherwise freshness expires after
  `max(60, 2 × cadence_seconds)` seconds. Receipt never establishes freshness.
- Observations beyond five minutes into the future or outside raw retention
  are rejected. Unknown fields, including device URLs/commands, are rejected.

There are 60 authenticated requests per installation per minute, including
invalid payloads, plus a 120 POST/minute IP limit. A 429 has `Retry-After: 60`.
Batch writes are atomic. Sequences are strictly increasing positive 64-bit
integers. Same ID, sequence and normalized contents are an idempotent retry;
they do not change receipt time or statistics. Changed duplicates, unknown
sequences at/below the watermark and out-of-order batches fail with 409.
The watermark survives sample expiry and credential rotation. After deletion,
the former credential and installation no longer exist.

Hourly aggregates contain min/max/mean/count grouped by metric, unit,
direction, location, quality, device, source and cadence. They are descriptive
statistics, including for counters; there is no power integration, counter
differencing, tariff application or billing conversion. Unknown observation
times produce no aggregate. Buckets use source time, never receipt time.

## Local forwarder

`telemetry_collector.py` accepts normalized samples on stdin, persists them in
a private SQLite spool and forwards to a single locally configured endpoint.
It has no listener or device adapter. Use a private configuration file (0600)
and private spool directory (0700):

```json
{
  "installation_id": "c3fc9e27-60af-460b-8b83-ec81d533dcb9",
  "source": {
    "name": "Roof",
    "device_id": "inverter-1",
    "source_id": "local-reader",
    "cadence_seconds": 30
  },
  "spool": "/var/lib/openleg-collector/queue.sqlite3",
  "endpoint": null,
  "allowed_endpoints": [],
  "last_sequence": 0
}
```

This is local-only mode; it never sends a request and needs no credential.
To forward, set `endpoint` to the exact installation ingestion URL, add that
same URL to `allowed_endpoints`, and add the issued `credential`. External
endpoints require HTTPS with certificate validation; plain HTTP is accepted
only for literal loopback IPs. Redirects and environment proxies are disabled.
Allowlisting a different path cannot enable commands or general URL requests.

```sh
python telemetry_collector.py /private/collector.json enqueue < observation.json
python telemetry_collector.py /private/collector.json flush
```

The stdin object contains the measurement fields from the sample example,
without `sample_id`, `sequence`, `device_id`, `source_id` or `cadence_seconds`.
The collector supplies identity and deduplication fields and preserves a null
observation timestamp. It does not replace it with local or receipt time.

Queue limits are 10,000 samples, 8 KiB per sample, 24 hours of queued age and
32 MiB of SQLite pages. A full queue rejects new samples; expired entries are
removed on enqueue/flush. It sends at most 100 samples/64 KiB per flush, with
source observations limited conservatively to the server's minimum one-day
retention, regardless of `source.raw_retention_days`. Before forwarding it also
drops observations approaching that limit, with a 30-second transit margin.
Changing server retention therefore cannot strand newer samples behind an
older buffered observation. It sends each batch with
a 10-second HTTP timeout. Retry state survives restart: 2-second exponential
backoff capped at 300 seconds, at most eight attempts. Authentication,
redirect, scope and validation failures block further automatic forwarding.
429, timeout and server errors retry. Counts are removed only after a valid
acknowledgment; IDs, sequences and observation timestamps survive retries.

Run `flush` from a local timer (for example every 30 seconds), including during
outages, so queue age cleanup continues. Stopping the collector also stops its
local cleanup. After correcting a blocked batch or rotating a credential,
`resume` explicitly resets retries. Replay conflicts require investigating the
spool; do not rewrite old observation times. Keep the spool across rotations;
when starting a new spool, set `last_sequence` to the server's `highwater`.
One spool belongs to one installation/source identity.

## Retention and operations

Raw data defaults to 7 days, hourly statistics to 90 days. Owners can select
1–7 raw days and 1–90 aggregate days. These are product defaults, not legal
retention claims, and do not alter billing retention. Raw expiry uses both
observation and receipt time; missing observations use receipt only to expire
data. Aggregates expire by observation hour. Expired rows are hidden even
before physical cleanup.

Schedule a daily POST to `/api/cron/cleanup-telemetry` with `X-Cron-Secret` on
each deployment. This is a required rollout step; merely registering the route
does not install an external scheduler. The response contains deleted counts.
Profile, membership or community deletion cascades telemetry. Installation
deletion removes the highwater too; expiry alone preserves it against replay.
Backups follow the deployment's separate backup lifecycle.

Application errors expose fixed codes, with no samples, credential values or
SQL parameters. Collector output contains only states and counts. Reverse
proxies must not log Authorization headers or request/response bodies; the
enrollment/rotation response contains a one-time secret. Device credentials
stay in the local adapter and never belong in these payloads.
