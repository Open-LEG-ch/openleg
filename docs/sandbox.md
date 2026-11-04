# Fictional village sandbox

Sonnwil is a disposable, fictional village. Visitors choose a demo account and
change the real product's state in a private village copy. No personal signup
is required. The prototype currently supports the resident energy-profile
journey and a simulated inbox. The remaining journeys are tracked in #768.

## Run locally

Use Linux, PostgreSQL 16 binaries, `libseccomp.so.2` and the normal Python
dependencies from `requirements-dev.txt`. The sandbox starts its own scratch
PostgreSQL cluster. It uses no existing database or Redis service.

From the activated project environment, run:

```sh
python scripts/sandbox.py
```

Open `http://127.0.0.1:5090/sandbox`, choose the household account, change the
annual consumption on the dashboard and save. Refresh to see the stored value.
Open the same address in a separate browser profile for an independent village.
Dashboard login emails appear in the simulated inbox linked in the banner.

The default is four concurrent villages, a two-MiB upload limit per request and
a one-hour session lifetime. Expiry is checked on access or when another village
is created; an idle village can remain running longer. Automatic cleanup and
inactivity limits are tracked in #770. Use `--max-workspaces`, `--max-upload-mib`,
`--lifetime-seconds` and `--port` to choose different local bounds. Uploads can
be bounded from one to ten MiB through the CLI. `--postgres-bin` selects the
PostgreSQL 16 binary directory; its default is `/usr/lib/postgresql/16/bin`.
The public origin defaults to the loopback listening URL. Set `--origin` when
using a proxy. Other Host headers are rejected.

Press Ctrl+C in the runner terminal, or run `python scripts/sandbox.py --stop`
from another terminal, to stop the sandbox and remove its village databases,
files and processes. The directory belongs only to this scratch
runtime; never point it at production storage. Browser reset and unattended
expiry recovery are tracked in #770. A stale runner record does not block a
new start; a record identifying a live runner refuses a second start. Full crash
resource recovery remains part of #770. Missing `.scratch` parents are created
with private permissions, including under a shared group umask. Existing
writable parents are rejected. The error names the rejected path. For an owned
checkout created with group write permission, remove that permission with
`chmod g-w /path/named/by/the/error` before restarting. The runner preserves
an inherited ignored SIGHUP, so `nohup` continues to work.

## Boundaries

Each village has a dedicated application process, database owner, database,
session signing key, working directory and in-memory cache. Product requests
always use that process's ordinary `database.get_connection` seam. They cannot
select a database using a hostname, object ID or request parameter.

The gateway owns provisioning credentials. A worker receives only its own
database credential and an explicit allow-list of environment settings. Its
Gunicorn control socket is disabled. The kernel refuses all non-Unix sockets
before application integrations are imported. PostgreSQL and product HTTP use
private Unix sockets. Startup fails if the network boundary or scratch database
cannot be established. The filter also denies the io_uring interface, which
can otherwise create sockets without a socket syscall. The runner probes the
filter in a separate process before starting PostgreSQL. Expected worker startup
dependency failures return a generic unavailable page without private diagnostics.

Mail is captured locally. The browser's content security policy permits only
same-origin resources and blocks external forms and frames. The sandbox uses
local assets and no analytics. External signatures, VNB submissions, bank
operations and device services are unavailable. Later scenario adapters must
preserve that boundary and use the ordinary product domain operations.

The gateway is a separate entry point. The normal product application does not
install demo authentication routes. This prototype verifies synthetic behavior;
it supplies no real VNB, hardware, accounting or legal acceptance.

## Verification

```sh
pytest tests/test_sandbox_http.py tests/test_sandbox_guards.py -q
scripts/tdd_cycle.sh gate
```

The HTTP tests start real PostgreSQL and independent application processes.
They require local PostgreSQL 16 binaries. CI uses the Ubuntu 24.04 runner with
those binaries and explicitly checks their presence before running the suite.
The process-boundary test attempts real socket creation under the kernel filter.

The first-slice validation also deliberately disabled workspace routing, unique
session keys, private mail storage, explicit mode, Host and CSRF checks, the
network filter and database role/connection restrictions. Each change made its
regression fail. Restoring the guards returned the focused suite to green.
The implementation PR records the results; this is not a suite-wide mutation score.
