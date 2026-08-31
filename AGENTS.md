# OpenLEG Guide

## Project

OpenLEG is free, open-source infrastructure for Swiss Lokale Elektrizitätsgemeinschaften (LEG).  
Mission: maximize functioning LEGs, maximize autarky, minimize costs, never sell citizen data.

## Repository Boundary

This repository is the **public app repo** (`openleg`).

- Product runtime code, tests, and public docs stay here.
- Private infrastructure, production operations, and internal strategy are moved to `openleg-ops`.
- Private ops procedures live in `openleg-ops`.

## Public Repo

Use this repo for:

- Flask app and API code
- Templates/static assets
- Database layer and migrations
- Test suite and CI workflows
- Public-safe docs and contribution workflows

Public repo constraints:

- No production credentials or secret-bearing files
- No production host inventory or incident runbooks
- No internal strategy packets, grant drafts, or outreach execution notes

## Private Ops Repo

Use `openleg-ops` for:

- Deployment runbooks and production checklists
- Environment-specific overrides and host inventory
- Secret handling procedures and key rotation notes
- Incident response notes and operational timelines
- Internal strategy, grant, and outreach execution materials

## Swiss German Text Rules

All user-facing German text must use Schweizer Hochdeutsch:

- Use real umlauts: `ä`, `ö`, `ü` (never `ae`, `oe`, `ue`)
- Use `ss` instead of `ß`
- Use active voice
- Do not use em dash or en dash
- Keep URLs and code identifiers in ASCII

## Architecture (Public)

- Backend: Flask (Python 3.11)
- Database: PostgreSQL 16
- Cache: Redis 7
- Reverse proxy: Caddy
- Domains: `openleg.ch`, `<city>.openleg.ch`, `api.openleg.ch`

Domain vocabulary lives in `CONTEXT.md`. Use those module/seam names.

## Data Layer

- `database.py` owns the connection seam (`get_connection`) plus schema/migrations.
- Self-contained domains are extracted into per-domain repositories under `store/`,
  each resolving the seam via `database.get_connection` and re-exported from
  `database.py`, so `import database as db; db.<fn>()` keeps working unchanged.
  Shipped: `store/ranking` (PV/Rangliste), `store/profile` (municipality energy
  profile: ElCom tariffs, profiles, Sonnendach), `store/billing` (LEG community
  billing), `store/email_queue` (outbound email queue), `store/utility` (EVU/VNB
  utility clients), `store/metering` (SDAT metering points, 15-minute E66
  readings, import ledger).
- New storage code for a cohesive domain goes in `store/`, not into `database.py`.
- Deepening roadmap and next extraction order: `docs/architecture.md`.

### Neighbour consent gate

`share_with_neighbors` lives in the `consents` table, not in `buildings`, so
`WHERE verified = TRUE` alone does **not** respect consent. Registration requires
both consents, but residents may revoke later from their dashboard, and the
dashboard promises they can.

Any query whose output another resident can see must add:

```sql
INNER JOIN consents c ON b.building_id = c.building_id
AND c.share_with_neighbors = TRUE
```

Inner join, so a building with no consent row is excluded. Fail closed.

When a query returns both a filtered list and a count over the same rows, the
count must use filter conditions **identical** to the list's. A count that
disagrees with its own list discloses that hidden members exist.

Queries already gated: `get_neighbor_count_near`, `get_all_buildings`,
`formation_wizard.get_formable_clusters`,
`store.referral.get_referral_leaderboard`.

When a query turns out to have no consumer, prefer deleting it to gating it. That
removes the exposure rather than narrowing it, and it cannot break a caller that
does not exist. Two unauthenticated endpoints published resident locations that
nothing had ever fetched; they were deleted rather than hardened.

### One savings model

`formation_wizard.calculate_savings_estimate` is the only savings calculation. It
states its basis through named module-level constants in `formation_wizard.py`,
read at call time so a monkeypatched value reaches the response:

- `DEFAULT_GRID_BUY_PRICE_RP` (25), `DEFAULT_GRID_SELL_PRICE_RP` (6),
  `DEFAULT_LEG_PRICE_RP` (15) and `DEFAULT_SELF_CONSUMPTION_SHARE_PCT` (30) are the
  price and consumption assumptions.
- `DEFAULT_SOLAR_KWH_PER_KWP` (950) is the production assumption and the only one
  shared across modules: `api_public.py`, `app.py` and `tenant.py` all import it, so
  the yield cannot diverge again. A per-tenant `solar_kwh_per_kwp` still overrides it.

The function returns all of these in its `assumptions` dict, and the dashboard
renders them from the response rather than hardcoding any.

Never add a second calculation. Two surfaces once answered the same question with
CHF 180 and CHF 135. A test pins the endpoint and the function to the same figure;
if it fails, it is telling the truth.

## Templates and Pathways

- Public entry contract: anonymous `GET /` renders the public website;
  `GET /login` renders the Eigentümer/Gemeinde dashboard chooser. An authenticated
  session may redirect `/` to its dashboard. Changes to root routing, public
  templates, `PUBLIC_SITE_URL`, proxying, or release assets must follow
  `docs/public-site-release-contract.md`.
- Product pages extend `templates/product_base.html` and use
  `partials/tailwind_brand.html` (built CSS, never the Tailwind CDN). The product
  shell has no marketing navigation or footer.
- Tailwind is compiled from `static/css/tailwind.css` to `static/css/openleg.css`
  with pinned dependencies: run `npm ci`, then `npm run build:css`. Rebuild after
  adding new utility classes.
- Product pathways: `/dashboard` (owners/founders), `/leg/dashboard` (LEG
  operators), `/gemeinde/dashboard` (municipalities), `/utility/login` (VNB/EVU),
  and `/api/v1/docs` (developers).
- Marketing pages, public directories, ranking pages, legal pages, and their
  assets live in this public app repository. `openleg-ops` owns only their
  production deployment. `PUBLIC_SITE_URL` is a link-generation seam, not a
  transfer of public-site ownership.

## Development Workflow

Pipeline:

`Idea -> Research -> Prototype -> PRD -> Kanban -> Execution -> QA`

Execution standard:

- Use small slices
- TDD first: red -> green -> refactor
- Prefer `scripts/tdd_cycle.sh` for deterministic loop commands
- Run full regression gates before merge

Tests must be able to fail. A fake that accepts the *shape* of a query as proof of
its behaviour proves nothing: doubles that treated any `JOIN consents` as a consent
filter would have passed while production leaked revoked buildings. Assert the
predicate, not the join.

Verify any security or privacy assertion by mutation: break the production code the
test is meant to catch, confirm the suite goes red, revert, confirm green. Report
the red output. An untested guard is worse than none, because it is trusted.

## Agent Execution

The `Execution` stage runs as an orchestrator-executor loop. The primary agent
plans slices, writes or approves failing tests, reviews every hunk, drives the
real app, and verifies all gates. Kimi Code implements almost all execution
tasks through the project-local CLI. The primary agent edits directly only for
tiny mechanical changes. If Kimi Code is unavailable, use Claude Code. If both
are unavailable, use ChatGPT 5.4. Record every fallback and its reason.

- One slice, one issue, one `codex/<slug>` branch, one `[codex]` PR.
- Red tests first; Kimi Code iterates until the full suite is green; the
  orchestrator reviews every hunk and drives the real app before shipping.
- **A green suite does not mean the change stayed in scope.** Executors widen
  silently: one rewired `/api/calculate_savings` to a different model, changing the
  advertised saving for a 10 kWp household from CHF 700 to CHF 1080 and dropping an
  output cap, with every test passing. Read the diff for edits nobody asked for
  before trusting the gates.
- Stage explicit paths. `git add -A` has twice swept unrelated files into a commit
  (another author's work in progress, and a stub `uv.lock`).
- Run CodeRabbit on every slice before opening or updating its PR. Address all
  actionable findings and rerun until clean. A rate limit may delay review but
  never permits shipping without it.
- The local `coderabbit` CLI and the GitHub app do not find the same issues: the CLI
  reported "no findings" on branches the app then flagged, including a privacy
  contradiction on a consent form. The app is the gate that counts. The CLI
  rate-limits for 20 to 45 minutes, and a check reading `pass ... Review rate
  limited` means the commit was **not** reviewed.
- Branch protection requires conversation resolution, so unresolved review threads
  block the merge even when every check is green. Read each thread, fix it or reply
  with the reason you declined, then resolve it. A formal `CHANGES_REQUESTED` review
  keeps blocking until a later review supersedes it.
- Deviating from a review suggestion is fine when it is wrong for the context; say so
  in the thread rather than resolving silently.
- `scripts/tdd_cycle.sh gate` must pass before every PR; merge only via PR,
  never push to `main`. QA stays human.
- Cloud-task alternative: Codex environments use `scripts/codex_setup.sh`
  as setup script; tests are fully mocked and need no network or secrets.
- `AGENTS.md` is the agent contract and stays byte-identical to this file.

## CI and Branch Policy

`main` is PR-only.  
Required checks are exactly:

- `ci/lint`
- `ci/test`
- `ci/security`

No direct push to `main`.

Mainline workflows are locked to exactly three (`ci/lint`, `ci/test`, `ci/security`)
by `tests/test_ci_contract.py`. Any other workflow (for example the image publish)
must trigger off-mainline (tags or `workflow_dispatch`), never on `push: main`.

## Development Commands

```bash
python app.py
pytest tests/ -q
ruff check .
ruff format --check .
```

Ruff is pinned to `ruff==0.16.5` in `requirements-dev.txt`. A system ruff on an
older version reports about 30 phantom `E402`s that CI never sees, so check your
version and run `uvx ruff@0.16.5 ...` when it differs.

`uv` is not this project's dependency manager: CI installs from `requirements.txt`,
and `uv run` drops a stub `uv.lock` that pins nothing and claims the wrong Python
version. It is gitignored. Using `uvx` for the pinned ruff is still correct.

The app needs Redis and PostgreSQL, so it will not boot from a bare checkout. The
suite is fully mocked and needs neither. To check rendering without the services,
call the real view-model function and render the template through Jinja directly.

## Deployment in Public Repo

Use only public-safe deployment template and examples:

- `deploy.example.sh`
- `.env.example`

Container image: built from `Dockerfile`, published to
`ghcr.io/open-leg-ch/openleg` on `v*` tags (and `workflow_dispatch`) by
`.github/workflows/image.yml`. Self-hosters pull the image via `docker compose`;
see the README install profiles. Tag a release (`git tag -a vX.Y.Z`) to publish.

The public repository never deploys production. The image workflow publishes
version/SHA tags, SBOM, provenance, and the immutable digest. Production release
approval, database backup, digest promotion, health verification, ledger, and
rollback belong exclusively to private `openleg-ops`.

Production deployment procedures are documented in `openleg-ops`.

A release is not complete until the bounded public-site smoke checks in
`docs/public-site-release-contract.md` pass against production. Never replace
those checks with an unbounded crawl of municipality profile links.

## Current Blocker

- The AgentMail webhook receiver lives in this repo at `/api/internal/agentmail` and fails closed without `AGENTMAIL_WEBHOOK_SECRET`.
- Register the webhook with `scripts/register_agentmail_webhook.sh`.
- The agent gateway lives in `openleg-ops`.

## Data Policy

Citizen smart meter data stays within each LEG.  
Data is not sold and not aggregated for third parties.

Consent is revocable in fact, not only in the interface. Every surface honours the
current value of `share_with_neighbors`, not the value given at registration. See
the neighbour consent gate in the Data Layer section.

Never let the interface promise more than the system delivers. Each of these
shipped and had to be corrected: a privacy note promising no third-party sharing
directly above a checkbox authorising exactly that; a deletion promise wider than
what unsubscribe can reach; read-only buttons labelled with actions they do not
perform. Before writing a claim about data, check that the code keeps it.

Numbers shown to residents carry their basis. Readiness scores name their
components, savings estimates list their assumptions, and the Gemeinde scores link
to the public site's `/rangliste/methodik` through `PUBLIC_SITE_URL`. A figure a
reader cannot trace is a figure they cannot check.
