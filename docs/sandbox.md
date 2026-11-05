# Fictional village sandbox

Sonnwil is a disposable, fictional village. Visitors choose a demo account and
change the real product's state in a private village copy. No personal signup
is required. Household profiles, fictional role switching, community creation,
LEG access controls, municipality demand, utility preferences and API credentials
are available. The simulated inbox and village reset keep these exercises local.
The remaining journeys are tracked in #768.

The formation guide lets Mira create a LEG, invite Lea and Noah, and confirm
each household through the ordinary dashboard. Invitations appear once in the
captured inbox. Review the generated PDFs, then simulate each household's
signature on that exact document bundle. Changed participants require new drafts.
Every downloaded formation document carries a simulation marker.

Simulate delivery, rejection or receipt acknowledgement from the guide. These
actions store normal VNB evidence and audit records; acknowledgement does not
authorize operation. Download the fictional start PDF, attach it to an incoming
correspondence entry, then confirm its reference and effective date through the
ordinary start controls. Only an administrator can activate on or after that date.
A rejected submission stays rejected and offers a fresh LEG or village reset.

After activation, invite Sam, download the fictional join agreement and upload
it as outgoing correspondence. Submit the dated join with that source agreement,
then simulate its VNB response. Separately switch to Sam to confirm membership.
VNB processing does not change community-controlled membership. Repeating the
same proposal retains its original facts and evidence.

Sam and Robin can open the metering exercise without first forming a LEG. Five
dated synthetic metering assignments belong to the seeded active community.
The downloads contain a complete September 2026 in gzip-wrapped E66 XML. The
browser upload expands at most eight MiB and uses the normal E66 parser and
atomic evidence, physical-readings and import-ledger command.

The half-open Europe/Zurich period runs from September 1 at 00:00 to October 1
at 00:00, or August 31 at 22:00Z to September 30 at 22:00Z. Its 2880 intervals
each contain four consumption series of 0.250 kWh and one production series of
0.500 kWh. For every consumer the source split is 0.125 kWh community and
0.125 kWh grid. Production is entirely local. The no-battery monthly totals are
2880 kWh consumption, 1440 kWh production, 1440 kWh local consumption and
1440 kWh grid consumption. These deliberately flat profiles are invented
arithmetic fixtures, not real readings or solar forecasts.

Each source carries total, community and grid channels, sender/receiver IDs,
creation time, an external community ID and dated point bindings. Duplicate
downloads contain identical source bytes and replay the existing delivery.
Missing intervals, unmapped points and unsupported precision remain refused.
The interface explains each repair; it never substitutes physical telemetry.
Safe attempt receipts are private village files and do not count as accepted
billing evidence.

The correction changes production to 0.600 kWh per interval, with 0.150 kWh
local and 0.100 kWh grid consumption per household. Its monthly totals are
1728 kWh production/local consumption and 1152 kWh grid consumption. Import
requires explicit withdrawal of the original accepted source. Withdrawal
retains original bytes, identity and audit history; replay cannot reactivate
it. The normal replacement command retains the withdrawal lineage.

The scheduled exercise uses the ordinary due-date check, advisory lock, bounded
retry and PostgreSQL report. Its injected transport reads only a generated
source under this worker's data directory, and its importer uses the same strict
E66 evidence command. No FTPS credentials are needed. Trigger a failed fetch,
then explicitly retry without failure. A second same-day scheduled tick does
not rerun the job; explicit retries preserve delivery and reading identities.

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

Use “Konto wechseln” in the banner to choose another fictional account. Lea and
Noah have household profiles, Mira can create a LEG, Robin administers LEG
Sonnwil, and Sam has metering and billing-preparation roles without administration.
Jules sees aggregate municipality demand and can change the display language.
Kim can save the utility's email language and create or revoke demo API keys.
Each account's village page explains its starting state, action and expected result.

Changing accounts preserves village data and replaces the login context. Earlier
login cookies and stale privileged forms lose authority. This revocation survives
a product-worker reload. A successful ordinary magic-link login also renews
the binding and CSRF tokens when the selected account is unchanged. Rejected
links leave the current login intact. Returning to the entry form with an
existing village opens its account chooser; switching still requires the
worker-side form. The ordinary product permission rules still decide
which records and actions each account can use.

The village page also links to "Haushalt anmelden". Its editable address,
email and energy defaults are fictional offline fixtures. No geocoder or
address-enrichment service is called. The fixture coordinates meet the ordinary
Swiss input bounds; Sonnwil, its street names and BFS number remain fictional.

Register a household, open its confirmation link in the captured inbox, then
return to that message and request an access link. Open the new access message
to enter the confirmed household. Registration, confirmation, expiry and login
use the normal product routes, even when another demo account was signed in.

Save its energy inputs and use the dashboard savings button to inspect the
shared calculation and assumptions. Grant neighbour visibility, switch to Lea
and compare the nearby count and contact candidates. Revoke visibility and read
them again. Contact requests require an explicit field choice on both sides.
Use the inbox notification to identify the recipient's fictional account, then
accept, decline or revoke through its ordinary contact page. The private seed
household remains absent from discovery.

Profile downloads identify their synthetic origin in the filename and JSON.
Deletion requires a captured confirmation and removes the profile and consents;
ordinary billing-retention rules still apply. Old sessions and pending access
links stop working. GET and HEAD only display confirmation. POST rotates the
village login after successful deletion, before the database transaction commits.
A failed login-generation write rolls back the deletion and returns HTTP 503.
The private fixture manifest records each original demo
household's server-generated registration identity. The picker checks that
identity as well as verification status. Deleting and recreating a building ID
does not recreate its original demo persona. The replacement uses its own
captured access link and appears as an additional household. The manifest stays
unchanged across worker reloads and is removed with the village. Reset the
village to restore its original accounts.

The defaults are four concurrent villages, a two-MiB upload limit per request,
a 15-minute inactivity timeout, and a one-hour maximum lifetime. A background
cleanup task retires expired villages and returns their capacity. Use
`--max-workspaces`, `--max-upload-mib`, `--inactivity-seconds`,
`--lifetime-seconds` and `--port` to choose different local bounds. Uploads can
be bounded from one to ten MiB through the CLI. `--postgres-bin` selects the
PostgreSQL 16 binary directory; its default is `/usr/lib/postgresql/16/bin`.
The public origin defaults to the loopback listening URL. Set `--origin` when
using a proxy. Other Host headers are rejected.

Press Ctrl+C in the runner terminal, or run `python scripts/sandbox.py --stop`
from another terminal, to stop the sandbox and remove its village databases,
files and processes. The directory belongs only to this scratch
runtime; never point it at production storage. It must be private and owned by
the runner. Missing `.scratch` parents are created with private permissions,
including under a shared group umask. Existing writable parents are rejected.
The error names the rejected path. For a shared checkout created with group
write permission, remove that permission with `chmod g-w /path/named/by/the/error`
before restarting. The runner preserves an inherited ignored SIGHUP, so `nohup`
continues to work.
A filesystem lease prevents two gateways from using the same root. A stale
runner record does not block a new start; a live record refuses a second start.

## Reset and expiry

Use “Dorf verwalten” in the banner to reset with a deliberate confirmation or
discard the village. Reset creates a fresh copy of the selected seed version;
discard creates no replacement. Every product URL includes its generation.
Old tabs receive HTTP 410 and cannot write into the replacement, even if the
browser now holds its new cookies.

An older, incomplete or lost fixture without its original household identity
proofs cannot resume a product worker. Use the gateway's village management page to
reset it. Reset creates new proofs with the new accounts; current database rows
are never adopted as proof of the original fictional accounts.

Retirement waits for an in-flight request before stopping its worker and
removing its database, role and working directory. Mail, uploads, documents,
webhook captures, caches and restore destinations belong under that directory.
Failed cleanup stays inactive, reserves its capacity and records a bounded
three-attempt retry. Restarting the gateway reclaims its old processes and
scratch storage. It never resumes old sessions. This prototype provides no
persistence guarantee across restarts.

PostgreSQL version and initialization commands have 60-second deadlines; start
and stop commands have 120-second deadlines. Repeated shutdown signals are
ignored while cleanup runs. Failed cleanup retains its runner record and scratch
resources for the next start to reclaim. Crash recovery validates process
ownership before reclaiming scratch
resources, including clusters with incomplete PostgreSQL PID files.

## Boundaries

Each village has a dedicated application process, database owner, database,
session signing key, working directory and in-memory cache. Product requests
always use that process's ordinary `database.get_connection` seam. They cannot
select a database using a hostname, object ID or request parameter.

Workers share the runner's OS user. The operating system does not separate their
files or processes. A compromised worker could read other workspace directories
and other workers' `/proc` environments, including their database credentials
and signing keys. The seccomp network filter does not restrict these reads.
Run only trusted, reviewed application code in this runtime. Keep production
credentials and production storage outside the entire sandbox service.

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
local assets and no analytics. Signatures and VNB responses use local synthetic
adapters installed only in village workers. Bank operations and device services
are unavailable. Scenario adapters preserve that boundary and use the ordinary
product domain operations.

The gateway is a separate entry point. The normal product application does not
install demo authentication routes. This prototype verifies synthetic behavior;
it supplies no real VNB, hardware, accounting or legal acceptance.

## Verification

```sh
pytest tests/test_sandbox_http.py tests/test_sandbox_guards.py tests/test_sandbox_lifecycle.py -q
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

## Fictional billing

Choose Sam and open `/sandbox/billing`. Import the complete September metering
fixture, then save the fully specified policy through the normal policy form.
The guide records independent expectations alongside actual source quantities
and saved draft positions. Sam runs the ordinary billing runner; Robin approves
through the normal guarded workspace. Repeated runs and approvals reuse the
stored period and invoices.

Before approval, withdrawing a source invalidates its draft. Importing the
correction and running again creates a new draft while preserving the invalid
one. The guide includes an unequal-total source and an unsupported battery
example to exercise normal refusal. There is no demo approval override.

Deliver issued invoices through the normal delivery command. Invoice mail stays
in this workspace and is visible only to its participant, including after a
persona switch. Other captured registration/access messages keep their existing
workspace scope. Members use the ordinary invoice pages and real PDF renderer.
HTML, PDF and JSON exports carry simulation labels. There is no payable QR or
bank destination in the fictional invoice. The ordinary cancellation command
retains invoice snapshots and lifecycle history; later tariffs do not rewrite
issued prices or quantities.
