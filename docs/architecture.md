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
| `registry_api_bp` | `leg_registry.py` | none |
| `public_api_bp` | `api_public.py` | `/api/v1` |
| `utility_bp` | `utility_portal.py` | `/utility` |
| `health_bp` | `health.py` | none |
| `admin_bp` | `admin.py` | none |
| `cron_bp` | `cron.py` | none |

## Route map

The public website, ranking pages, municipality profiles, registry directory,
legal pages, sitemap, and installer delivery run from `openleg-ops`. This app
does not register those HTML routes. `PUBLIC_SITE_URL` is the explicit link seam.

Application and API routes:

- `/` chooses the Eigentümer or Gemeinde dashboard. `/dashboard` and `/leg/*`
  drive registration, community formation, documents,
  and correspondence.
- `/meter-upload` accepts a meter file; `/api/meter-data/upload` ingests it.
- `/api/v1/*` is the unauthenticated public JSON API, documented at `/api/v1/docs`.
- `/api/cron/*` runs scheduled work behind a cron secret.
- `/api/cron/process-billing` processes the previous complete month for every
  active community behind that cron secret.
- `/api/billing/community/<community_id>/period/<int:period_id>` returns one
  persisted draft billing period as JSON to admins.
- `/admin/abrechnungen` renders the same persisted drafts as a read-only audit
  workspace with tariff, VNB reconciliation, provenance, and signed line items.
- `/admin/*` and `/api/internal/*` sit behind an admin or internal token.

## Code map

- `app.py`: Flask product app, security policy, and the application factory.
  It wires Flask; the configuration it hands Flask comes from `app_config.py`.
- `app_config.py`: the application configuration as a value. Environment
  parsing, the token TTL bounds, and the `PUBLIC_SITE_URL` origin validation,
  with no Flask import.
- `tenant.py`: hostname to territory resolution, tenant config, template context.
- `database.py`: connection pool, schema creation, unextracted query helpers,
  and the store re-exports.
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
- `billing_workspace.py`: display-ready audit model for persisted billing drafts.
- `sdat_datahub.py`, `sdat_e66.py`, `meter_data.py`: meter data retrieval,
  SDAT parsing, and upload ingestion.
- `templates/`, `static/`, `tests/`, `scripts/`.

## Data layer

`database.py` owns the connection seam `get_connection`, idempotent schema
creation, and the domains that have not been extracted yet. Self-contained
domains move into `store/`, each resolving the seam through a lazy
`database.get_connection` lookup and re-exported at the end of `database.py`, so
`import database as db; db.<fn>()` keeps working unchanged.

Shipped stores: `store/building`, `store/cluster`, `store/ranking`,
`store/profile`, `store/billing`, `store/email_queue`, `store/utility`,
`store/metering`, `store/meter`, `store/registry`, `store/tenant`,
`store/token`, `store/access_token`.

New storage code for a cohesive domain goes into `store/`, not into
`database.py`.

### Extraction order

Remaining in `database.py`, in the order they should be extracted. Each move is
mechanical: lift the functions, add the lazy `_get_connection`, re-export, and
keep the existing tests green.

1. `store/analytics` — events and aggregate stats.
2. `store/consent` — data consents and consent counts.
3. `store/document` — LEG documents and signing status.
4. `store/ops` — LEA reports and ops snapshots.

`_create_tables()` and `get_connection` stay in `database.py`. Extraction is
finished when nothing but the pool, the schema, and the re-exports remain.

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
for the previous complete month. A read-only operator UI exposes persisted drafts
for audit, but there is no member UI and no invoice PDF generation or download yet.

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
