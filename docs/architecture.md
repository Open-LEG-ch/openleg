# Architecture

OpenLEG is a Flask application for Swiss Lokale Elektrizitätsgemeinschaften.
This public repository contains product runtime code, tests, templates, public
docs, and CI. Production operations and secret handling belong outside this
repository.

Domain vocabulary and seam names: `CONTEXT.md`.

## Runtime

- Flask serves product dashboards, forms, health checks, and JSON API routes. `app.py`
  provides the app factory `create_app(config=None, *, load_environment=True,
  check_database=True)`, which builds one configured `Flask` app and registers
  the blueprints on it. `wsgi.py` is the production entry point.
- PostgreSQL stores registrations, municipality profiles, tariffs, PV ranking
  data, metering data, consent records, and operational app state.
- Redis backs rate limiting, the tenant config cache, and short lived state.
- Caddy terminates TLS and proxies traffic to the Flask container.

The app refuses to start without a database. `create_app` raises
`RuntimeError("PostgreSQL required. Set DATABASE_URL.")` unless it is called with
`check_database=False`, which is what the tests do.

Scripts are the exception, and the trap is there. `database.init_db()` only
returns `False` when `psycopg2` is missing or `DATABASE_URL` is unset, and the
module reads `DATABASE_URL` at import time. A script must therefore call
`load_dotenv()` before importing `database`, or it runs against no database while
looking like it worked.

## Multi-tenancy

`tenant.init_tenant_middleware` registers a `before_request` hook that maps the
request hostname to a territory slug:

- `dietikon.openleg.ch` resolves to `dietikon`
- `openleg.ch`, `www.openleg.ch`, and `localhost` resolve to `zurich`
- Reserved service labels such as `api` and `admin` are not tenants.

The resolved config lands on `g.tenant` and is injected into every template by a
context processor. `render_city_template` prefers
`templates/cities/<territory>/<name>.html` and falls back to the shared
template, so a municipality gets branding, map bounds, and page overrides
without code changes.

## Blueprints

| Blueprint | Module | Prefix |
| --- | --- | --- |
| `main_bp` | `app.py` | none |
| `municipality_bp` | `municipality.py` | `/gemeinde` |
| `pilot_bp` | `municipality.py` | `/pilotgemeinde` |
| `registry_api_bp` | `leg_registry.py` | none |
| `public_api_bp` | `api_public.py` | `/api/v1` |
| `utility_bp` | `utility_portal.py` | `/utility` |
| `health_bp` | `health.py` | none |
| `admin_bp` | `admin.py` | none |
| `cron_bp` | `cron.py` | none |
| `rangliste_bp` | `rangliste.py` | `/rangliste` |
| `self_host_bp` | `self_host.py` | `/self-host`, `/install.sh` |

## Route map

The public website, ranking pages, legal pages, sitemap, and installer delivery
run from this public app. Authenticated dashboards remain separate product
routes. `PUBLIC_SITE_URL` is the explicit link seam for product templates.

Application and API routes:

- `/` renders the public homepage. `/login` chooses the Eigentümer or Gemeinde
  dashboard. `/dashboard` and `/leg/*` drive registration, community formation, documents,
  and correspondence.
- `/meter-upload` accepts a meter file; `/api/meter-data/upload` ingests it.
- `/api/v1/*` is the unauthenticated public JSON API, documented at `/api/v1/docs`.
- `/api/cron/*` runs scheduled work behind a cron secret.
- `/api/cron/process-billing` processes the previous complete month for every
  active community behind that cron secret.
- `/api/billing/community/<community_id>/period/<int:period_id>` returns one
  persisted draft billing period as JSON to admins.
- `/leg/community/<community_id>/billing` is the admin-gated approval
  workspace: a confirmed community admin reviews a reconciled draft and
  approves it, which issues one immutable invoice snapshot per participant.
  The same workspace delivers an issued invoice through its frozen channel,
  records payment, cancels unpaid invoices, links separately approved
  corrections, and displays the append-only lifecycle audit.
- `/admin/abrechnungen` renders the same persisted drafts as a read-only audit
  workspace with tariff, VNB reconciliation, provenance, and signed line items.
- `/dashboard/invoices` is the private member invoice list: an authenticated
  dashboard session sees only their own issued invoices.
  `/dashboard/invoices/<int:invoice_id>` renders one invoice's issuer, period,
  number, issue/due dates, VAT treatment, and charges/credits/total, and
  `/dashboard/invoices/<int:invoice_id>/pdf` downloads the identical figures
  as a PDF. Every value
  is read from the frozen, immutable invoice snapshot columns (`policy_
  snapshot`, `provenance_snapshot`, `line_items_snapshot`, `net_chf`,
  `vat_chf`, `gross_chf`) through `member_invoices.py`; nothing is
  recomputed from mutable billing tables or the current policy. A missing
  invoice_id and another participant's invoice_id return the identical 404;
  a corrupted snapshot or a storage outage both fail closed to a
  non-disclosing 503, never an invented value. All three routes are
  session-gated and `Cache-Control: no-store`.
  New invoices freeze the issuing LEG's ID and name in the provenance snapshot;
  older invoices show their already-frozen `community_id` and never consult a
  mutable community name.
- `/admin/*` and `/api/internal/*` sit behind an admin or internal token.

## Code map

- `app.py`: Flask product app, security policy, and the application factory.
  It wires Flask; the configuration it hands Flask comes from `app_config.py`.
- `app_config.py`: the application configuration as a value. Environment
  parsing, the token TTL bounds, and the `PUBLIC_SITE_URL` origin validation,
  with no Flask import.
- `tenant.py`: hostname to territory resolution, tenant config, template context.
- `database.py`: connection pool, the `get_connection` seam, schema creation,
  and the store re-exports. It holds no queries of its own.
- `store/`: per-domain repositories (see the data layer below).
- `api_public.py`: unauthenticated public JSON API.
- `municipality.py`: municipality onboarding, access, and dashboard routes.
- `leg_registry.py`: registry federation API and verification operations.
- `utility_portal.py`: EVU and VNB portal.
- `access_token.py`: magic-link access policy. One module, two kinds: the
  dashboard building and the municipality. `store/access_token.py` holds the SQL.
- `cron.py`: the scheduled-job surface. Every route on it requires
  `CRON_SECRET` and fails closed without one.
- `health.py`: health and liveness endpoints.
- `public_data.py`: open data fetchers for ElCom, Energie Reporter, Sonnendach.
- `neighbor_view.py`: neighbour read policy: anonymity radius, jittered map locations, provisional match summary.
- `billing_engine.py`: energy allocation and network discount computation.
- `billing_readings.py`: validated billing frames and VNB reconciliation from
  imported quarter-hour readings.
- `billing_runner.py`: fail-closed orchestration and persistence for one billing
  period.
- `billing_approval.py`: fail-closed approval validation. Turns one reconciled
  draft period into immutable per-participant invoice snapshots, requiring the
  canonical reconciliation shape, SHA-256 provenance, and a complete policy
  snapshot inside the `billing_policy` value domains.
- `billing_lifecycle.py`: validates issued, delivered, paid, cancelled, and
  corrected state transitions without reading or writing storage.
- `billing_workspace.py`: display-ready audit model for persisted billing drafts.
- `member_invoices.py`: display-ready read model for one member's own issued
  invoices, built strictly from the frozen invoice snapshot; fails closed
  with `MemberInvoiceDataError` on a malformed or non-finite snapshot rather
  than rendering an invented value, and renders the identical PDF through the
  public `document_generator.render_pdf_html` seam.
- `sdat_datahub.py`, `sdat_e66.py`, `meter_data.py`: meter data retrieval,
  SDAT parsing, and upload ingestion.
- `templates/`, `static/`, `tests/`, `scripts/`.

## Data layer

`database.py` owns the connection seam `get_connection`, idempotent schema
creation, and the domains that have not been extracted yet. Self-contained
domains move into `store/`, each resolving the seam through a lazy
`database.get_connection` lookup and re-exported at the end of `database.py`, so
`import database as db; db.<fn>()` keeps working unchanged.

Shipped stores: `store/access_token`, `store/analytics`, `store/api_client`,
`store/billing`, `store/building`, `store/cluster`, `store/consent`,
`store/correspondence`, `store/dashboard_profile`, `store/document`,
`store/email_queue`, `store/formation_documents`, `store/meter`,
`store/metering`, `store/municipality`, `store/ops`, `store/profile`,
`store/ranking`, `store/referral`, `store/registry`, `store/tenant`,
`store/token`, `store/utility`.

New storage code for a cohesive domain goes into `store/`, not into
`database.py`.

### The extraction is finished

`database.py` now owns the connection pool, `get_connection`, the schema call,
and the re-export block, and nothing else. Every domain lives in `store/`.

New storage code for a cohesive domain gets its own module there. Nothing is
appended to `database.py`.

## Data pipelines

Two independent paths feed the database. Public-safe commands and required
environment-variable names are documented in `docs/data-pipeline.md`.

- **Public open data**: `public_data.py` and `scripts/load_pv_data.py` load BFE,
  BFS, ElCom, Sonnendach, and Energie Reporter data into
  `municipality_profiles`, `elcom_tariffs`, and the PV panel. This feeds the
  Rangliste, the Gemeindeprofil, and the public API.
- **Citizen meter data**: `sdat_datahub.py` and `scripts/fetch_sdat.py` retrieve
  SDAT files from the Swisseldex Datahub;
  `scripts/import_sdat.py` parses E66 messages through `sdat_e66.py` and writes
  `metering_points`, `metering_point_readings`, and the `sdat_imports` ledger.
  The `/meter-upload` page is a separate manual path that writes
  `meter_readings` per building.

`billing_readings.py` loads imported `metering_point_readings` into participant
frames. `billing_runner.py` validates tariff coverage, import provenance, and
VNB reconciliation, calls `generate_billing_summary`, and persists an immutable
draft `billing_periods` row. The secret-protected billing cron invokes this flow
for the previous complete month. Approval is the write seam: a confirmed
community admin approves a reconciled draft in the billing workspace, and
`store/billing.approve_billing_period` validates it through
`billing_approval.prepare_invoice_snapshots` and atomically issues one
immutable, timezone-aware `invoices` row per participant with frozen policy,
provenance, and line-item snapshots. `/admin/abrechnungen` stays a read-only
draft audit view. Admin lifecycle actions append actor, timestamp, previous and
new state, and supporting reason or reference to `invoice_lifecycle_events`;
they never update or delete the immutable invoice. Delivery uses a reservation
row around the external email boundary so retries never send a duplicate. If
the process cannot prove whether the external send completed, it neither sends
again nor changes the invoice state; the workspace requires the admin to check
the provider and explicitly confirm delivery. The member side is read-only
too: `member_invoices.py` turns one
issued invoice's frozen snapshot into the `/dashboard/invoices` list, its
private detail page, and its PDF download, without recomputing any figure. The
list and detail derive the current lifecycle status and correction links from
the append-only audit.

## Request flow

1. Caddy receives HTTPS traffic and forwards to Flask.
2. The tenant hook resolves the hostname and populates `g.tenant`.
3. Host, rate limit, and security middleware apply.
4. Route handlers read through `database.py` and the `store/` repositories.
5. Templates render HTML with tenant context and public-safe metadata, or API
   routes return JSON from stable read models.

## Verifying a guard

CLAUDE.md requires every security or privacy claim to be verified by mutation:
break the production code the test is meant to catch, confirm the suite goes
red, revert, confirm green.

`scripts/tdd_cycle.sh mutants` automates that check for the scope declared in
`[tool.mutmut]`, today `billing_runner.py` and `store/metering.py`: the two
places where a surviving mutant costs money or loses a meter correction. It does
not cover the whole repository, and it is not meant to. A guard outside that
scope, the neighbour consent gate or the access-token policy for instance, is
still verified by hand: break it, watch the named test go red, revert, and
report the red output in the pull request. Widen `source_paths` when a module
earns the runtime, never to make a score look better.

A coverage percentage over the `store/` layer is **not evidence**. Those tests
execute the surrounding Python while asserting only the shape of the SQL, so
coverage reads high while the predicate stays unverified. Only a behavioural
test, or a killed mutant, says anything about the query.

## Contribution boundaries

- Keep product code, public docs, tests, fixtures, and examples here.
- Do not add credentials, host inventory, incident runbooks, or internal plans.
  Those live in the private ops repository; see `docs/repo-boundary.md`.
- Prefer focused tests before implementation.
- Run `pytest tests/ -q`, `ruff check .`, and `ruff format --check .`.
