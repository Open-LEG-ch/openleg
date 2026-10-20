# Cached solar weather context (#620)

The owner readings page shows a separate 24-hour radiation forecast after current
measurements and installation notices. It uses public MeteoSwiss data and computes
no installation output, household surplus, savings or consumption schedule. No
tariff product or live contract is matched, so the page offers no monetary advice.
No proprietary weather icons are reused. German wording still needs pilot review.

## Source and time

[MeteoSwiss local forecasts](https://opendatadocs.meteoswiss.ch/e-forecast-data/e4-local-forecast-data)
provides `gre000h0`, hourly mean global radiation in W/m². Its Latin1, semicolon
CSV timestamps are UTC interval ends. Each displayed period starts one hour before
that end; both ends retain their Europe/Zurich zone and UTC offset across DST.
Missing intervals stay unavailable, and a known zero stays zero.

The source point is identified by `(point_type_id, point_id)`. Selection uses the
nearest postal-code point, with its name and distance displayed. The Swiss
collection bounding box and a 20 km product cutoff limit selection; that cutoff
is not validated installation-level spatial accuracy. No nearby point or no
coverage in the next 24 hours produces an unavailable state. Household
coordinates select the cached point locally and are never sent upstream or
written into the forecast cache.

Release time comes from the validated radiation asset filename, not the STAC
item's day-level timestamp. The page attributes MeteoSwiss and links the exact
source asset. A release older than three hours is stale; future releases are
unavailable. Refresh attempts never change the source release time.

## Refresh and cache

`POST /api/cron/refresh-solar-forecast` requires `X-Cron-Secret`. Schedule it every
15 minutes in the deployment; registering this route does not install a scheduler.
MeteoSwiss publishes hourly. A shared Redis lease permits one worker, and a
14-minute attempt key bounds retries after failures and allows scheduler jitter
before the next 15-minute tick. An unchanged complete
generation only fetches STAC metadata again, without downloading both CSV files.

The worker uses the fixed official HTTPS host, verified certificates, direct
connections without inherited proxies, and no redirects. Metadata is bounded to
2 MiB per response and radiation to 34 MiB. Connections have a five-second socket
timeout and a 20-second transfer watchdog per request; DNS uses the host resolver.
Both downloaded CSV files must match the SHA-256 checksums in official metadata.
The parser bounds rows, points and distinct intervals. It streams CSV rows and
retains only postal-point intervals ending after the release through release plus
28 hours. That deterministic window covers the 24-hour view throughout the
three-hour freshness limit and bounds application and Redis memory use.

Redis stores public point metadata and per-point intervals under source-checksum
generation keys. Entries expire after four hours. Publication follows all point
writes and atomically verifies lease ownership and release order. Failed or
partial refreshes preserve the prior published generation; a replaced worker
cannot delete its replacement's lease. Owner requests perform cache reads only.
Cache failure, invalid cached values, missing point data and stale releases leave
current measured readings available and show a separate unavailable weather state.

## Validation

Native Redis tests use `OPENLEG_TEST_FORECAST_REDIS_URL` on a disposable loopback
service. Each fixture creates and deletes its own random key namespace and never
flushes a database. CI supplies an isolated Redis 7 service. PostgreSQL-backed
route tests cover owner rendering, CSRF-protected notice actions and cron access;
parser tests cover identity, release selection, missing values and DST intervals.
A synthetic local browser check exercises German/French rendering and keyboard
acknowledgement on desktop and mobile layouts. It does not establish pilot
usability or the cross-browser accessibility evidence requested in #554.
