# Engineering Contract

This public document records the project rules that apply to every contribution.
Local agent instructions may add workflow details, but they do not replace these
requirements.

## Repository Boundary

This repository owns the public product runtime, API, templates, static assets,
database layer, migrations, tests, CI, and public documentation. Keep production
credentials, host inventory, deployment runbooks, incident records, internal
strategy, grants, and outreach execution in the private `openleg-ops` repository.
See `docs/repo-boundary.md` for the complete boundary.

## Swiss German Text

All user-facing German text uses Schweizer Hochdeutsch:

- Use real umlauts: `ä`, `ö`, `ü`, never `ae`, `oe`, or `ue`.
- Use `ss` instead of `ß`.
- Use active voice.
- Use a plain hyphen instead of an en dash or em dash.
- Keep URLs and code identifiers in ASCII.

## Architecture

The backend is Flask on Python 3.11, with PostgreSQL 16, Redis 7, and Caddy.
Domain vocabulary and module seam names live in `CONTEXT.md`.

## Data Layer

`database.py` owns `get_connection`, the schema, and migrations. Cohesive storage
domains live under `store/`, resolve the connection through
`database.get_connection`, and are re-exported from `database.py` when existing
callers use `import database as db`.

### Neighbour consent gate

`share_with_neighbors` lives in `consents`, not `buildings`. Verification alone
does not grant sharing permission, and residents may revoke permission after
registration. Any query whose output another resident can see must fail closed:

```sql
INNER JOIN consents c ON b.building_id = c.building_id
AND c.share_with_neighbors = TRUE
```

A filtered list and its count must use identical conditions. Otherwise the count
can reveal hidden members. Delete an unused query or endpoint instead of keeping
an unnecessary exposure.

### One savings model

`formation_wizard.calculate_savings_estimate` is the only savings calculation.
Its named module constants define the price, consumption, and production
assumptions. Callers share `DEFAULT_SOLAR_KWH_PER_KWP`; a tenant setting may
override it. The response exposes every assumption, and the interface renders
those values instead of hardcoding them. Do not add a second calculation.

## Public Site and Frontend

The public entry contract is: anonymous `GET /` renders the public website.
`GET /login` renders the
Eigentümer/Gemeinde dashboard chooser. An authenticated session may redirect `/`
to its dashboard. `PUBLIC_SITE_URL` is a link-generation seam, not a transfer of
site ownership. Routing, template, asset, proxy, or release changes must follow
`docs/public-site-release-contract.md`.

Product pages extend `templates/product_base.html` and use the shared brand
partial. The product shell has no marketing navigation or footer. The source CSS,
compiled asset, dependency commands, and no-CDN rule are documented in
`docs/frontend-build.md`.

The public app repository owns the marketing pages, public directories, ranking
pages, legal pages, and their assets. `openleg-ops` owns their production
deployment.

## Test Quality

Use small red, green, refactor slices. Start with a failing behaviour test and
keep the case that exposed a non-trivial bug. Never weaken, delete, or repurpose
an existing test to make a new change pass. Run `scripts/tdd_cycle.sh gate`
before merge.

Tests must prove behaviour. A query double that treats the presence of any
`JOIN consents` as proof of consent filtering can pass while production exposes
revoked rows. Assert the predicate and its building binding.

Verify every security or privacy assertion by mutation: break the production
code the test should catch, confirm the test fails, revert the mutation, and
confirm it passes.

## CI and Branch Policy

`main` is pull-request only. Its required checks are exactly `ci/lint`, `ci/test`,
and `ci/security`. Other workflows must run from tags, `workflow_dispatch`, or
another off-mainline trigger. Never push directly to `main`.

## Deployment Boundary

The public repository builds and publishes the container image from version tags
or `workflow_dispatch`; it does not deploy production. Production approval,
backup, digest promotion, health verification, release records, and rollback
belong in `openleg-ops`.

A release is complete only after the bounded smoke checks in
`docs/public-site-release-contract.md`. Do not replace them with an unbounded
crawl of municipality profiles.

## Data Policy

Citizen smart meter data stays within each LEG. Data is not sold or aggregated
for third parties. Every resident-facing surface honours the current
`share_with_neighbors` value, including revocation after registration.

Interface claims must match system behaviour. Numbers shown to residents state
their basis: scores name their components, savings estimates list assumptions,
and Gemeinde scores link to the public methodology through `PUBLIC_SITE_URL`.
