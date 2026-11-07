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

Robin can open the operator API exercise, issue a capability-scoped credential
through the ordinary product command and run editable formation/membership
requests. A prepared membership mutation records a proposal without changing
actual membership. Its mutation ID identifies identical replay; changed content
is refused. The console also demonstrates missing capability, foreign community,
rotated key and revoked key responses.

The reserved callback `https://sonnwil-simulation.invalid/webhook` names a local
in-process receiver. Sandbox credentials reject every other callback before DNS
lookup. Dispatch claims at most one delivery in Sonnwil's community, serializes
the ordinary event and verifies its real signature. The failed attempt returns
503; the ordinary retry command rearms it for a successful 204 attempt. Captures
contain the actual body, signature, verification and delivery result, with no
recoverable key. They are private workspace files removed by reset and expiry.
Issued keys and signing secrets appear once in the page, remain out of shared
logs and browser storage, and disappear on navigation. The console never sends
requests to an arbitrary URL. Production webhook validation remains unchanged.

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
webhook captures and caches belong under that directory. Linked restore
destinations own separate directories under the scratch root and retire first.
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
in this workspace and is visible only to its original participant registration,
including after a persona switch. Deleting and recreating an identifier or email
does not transfer historical invoices, questions, attachments or captured invoice
mail. Missing legacy registration references require operator reconciliation;
member access and email delivery fail closed. Draft persistence and issuance
compare and lock the original registrations. Other captured registration/access messages keep their existing
workspace scope. Members use the ordinary invoice pages and real PDF renderer.
HTML, PDF and JSON exports carry simulation labels. There is no payable QR or
bank destination in the fictional invoice. The ordinary cancellation command
retains invoice snapshots and lifecycle history; later tariffs do not rewrite
issued prices or quantities.

## Fictional bank reconciliation

After issuing the September invoices, choose Robin and open `/sandbox/payments`.
Deliver at least two positive invoices through the ordinary billing workspace.
The guide provides deterministic camt.053 and camt.054 statements for a selected
invoice: exact CHF credit, unknown reference, wrong amount/currency, debit and
reversal. The statement account identifier is `SIMULATION-SONNWIL-NO-BANK`.

The upload form uses the normal statement-import endpoint. For reviewed matches,
the sandbox control requires a confirmed administrator and CSRF proof, then calls
the same atomic command as the operator API. Its stable per-entry key keeps
retries bound to the selected invoice. Selecting an invoice cannot waive amount,
currency, credit/reversal or delivered-state eligibility. The ordinary invoice
lifecycle and operator outbox record successful payments; the guide reads those
scoped records without creating synthetic success events. An outbox receiver is
not required, and no bank connection or transfer is initiated.

Native regressions cover automatic and reviewed payments, statement replay,
concurrent same-entry and competing-entry confirmations, changed retry targets,
issued/cancelled invoices, CSRF, roles and existing foreign-community resources.
A second visitor's village has independent statement and invoice identities.

## Invoice questions

Open the invoice-question guide from the village home. Prepare September as
Sam, approve and deliver Lea's invoice as Robin, then switch to Lea. The normal
invoice form opens a question with an optional PDF of at most two MiB. The
fictional invoice's own PDF is suitable sample evidence.

Sam can acknowledge and resolve the case in the guide or use the ordinary
billing workspace. The guide calls the same request-bound keyed command as the
operator API. Repeat an unchanged key to inspect a replay; a changed request
with the same key or an invalid transition returns 409 and keeps the history.
A new answer uses a new key. Each accepted transition displays its real audit
identity and captured canonical invoice.case.updated event, without a remote
receiver. Switch back to Lea to read the replies and complete history.

The guide and PDF downloads recheck current invoice-owner or billing-preparer
scope. Other residents and operators lacking that capability cannot see the
case, messages or evidence. Workspaces remain independent. No case action
changes an issued invoice snapshot or sends external notifications.

## Household telemetry

The telemetry guide uses ordinary equipment forms and telemetry enrollment,
credential, ingestion and sharing commands. All device/source identities and
2500 W observations are fictional. It opens no device, household network or
broker connection and supplies no hardware compatibility evidence.

A local manual source publishes two bounded samples per request, power and a
boolean device-fault report. Replay resubmits the identical payload. Pause
prevents publishing; after at least 61 real seconds the ordinary one-second
cadence contract reports old samples as stale. Resume and publish restore fresh
samples. Device fault, acknowledgement and clearance use the normal notice
lifecycle. No operation changes the process clock or authentication expiry.

Successful controls redirect to the guide so browser refresh does not repeat
a write. Enrollment and rotation show the issued credential once. A private
receipt is consumed under the source lock; the signed session cookie carries
only its nonce and installation identifier. Even a copied pre-display cookie
cannot redisplay a consumed key. The source keeps its
current and immediately preceding credential, last payload and pause state in
private 0600 files under the worker's 0700 simulation-telemetry directory. A
per-installation file lock serializes source updates. Secrets never appear in
URLs, general logs, shared views or archives. Reset/expiry removes these files
with the rest of the workspace. Revocation leaves the old source credential
ineligible; rotation is required to resume delivery. The old-key test exercises
the same authentication check as ingestion.

Owner-only controls recheck current storage authorization. Noah's common
telemetry view also requires an explicit share and Lea's neighbour consent.
Revocation removes access on the next read. Private equipment details and notice
acknowledgement remain owner-only. Telemetry stays separate from E66 evidence,
billing periods and issued invoice snapshots.

## Community archive exercise

Robin can export the operating LEG after the metering, billing, payment and
question exercises. Mira can export a newly formed LEG with its original PDFs,
participant manifests and signature receipts. The normal administrator export
returns format 3, with its actual declared datasets and a synthetic filename
and manifest marker. Versions 1 and 2 remain readable with their older coverage.

Preview takes a fresh server-side export, creates an empty destination with the
same schema, and runs the ordinary archive dry-run. Restore uses that exact
private snapshot and the ordinary transactional restore engine. Each command
rechecks the source's current confirmed administrator and CSRF token. The gateway
supplies installation authority only for this linked scratch database; it never
exposes a platform token or changes the production restore endpoint. Users cannot
upload an edited archive through this exercise. The damaged-manifest choice
changes only a declared hash to demonstrate refusal, not provenance authentication.

The destination has its own database, owner, worker, cookies, signing key and
private directory. It occupies one of the existing workspace slots. Its directory
is a separately owned sibling under the scratch root, linked to the source's
lifecycle. Source retirement collects targets after in-flight operations finish;
expired sources cannot allocate new targets. A destination cannot reset itself
into an independent village. Its management page returns to the source, where
it can be discarded or the whole village can be reset. Startup crash recovery
reclaims every scratch directory without reviving either copy.

Open the restored LEG to compare normal invoice and source views. Repeating the
same snapshot imports no duplicate records. Change the restored account's energy
profile, return to the source and retry: the ordinary validator reports a conflict
without overwriting the target. A damaged manifest also imports nothing. Native
coverage injects a late invoice constraint failure and verifies that earlier
profile, community and billing inserts all roll back. A separate native journey
restores three actual participant receipts and original PDFs, then completes a
normal VNB submission against those restored records.

Snapshots are bounded to 50 MiB and stored with mode 0600 inside the source
worker's private directory. Explicit destination discard removes the snapshot;
source reset, discard and expiry remove both copies and their runtime files.
Live API credentials, pending webhooks, owner-only equipment and telemetry,
contact grants and deployment settings remain outside the archive. Restoring
never dispatches historical mail or webhooks. This exercise covers declared
community data, not complete deployment portability; broader facts remain #612.
