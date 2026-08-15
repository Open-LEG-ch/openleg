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
  utility clients).
- New storage code for a cohesive domain goes in `store/`, not into `database.py`.
- Deepening roadmap and next extraction order: `prd/architecture-deepening.md`.

## Templates and Pathways

- Every user-facing page uses the shared partials `partials/tailwind_brand.html`
  (built CSS, never the Tailwind CDN), `partials/site_nav.html`, `partials/site_footer.html`.
- Tailwind is compiled from `static/css/tailwind.css` to `static/css/openleg.css`
  with pinned dependencies: run `npm ci`, then `npm run build:css`. Rebuild after
  adding new utility classes.
- Stakeholder pathways: `/fuer-bewohner` (residents/founders), `/fuer-gemeinden`
  (municipalities), `/leg-gruenden` (LEG operators), `/open-source` (developers).
  README documents the matching per-stakeholder install profiles.

## Development Workflow

Pipeline:

`Idea -> Research -> Prototype -> PRD -> Kanban -> Execution -> QA`

Execution standard:

- Use small slices
- TDD first: red -> green -> refactor
- Prefer `scripts/tdd_cycle.sh` for deterministic loop commands
- Run full regression gates before merge

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
- Run CodeRabbit on every slice before opening or updating its PR. Address all
  actionable findings and rerun until clean. A rate limit may delay review but
  never permits shipping without it.
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

## Current Blocker

- The AgentMail webhook receiver lives in this repo at `/api/internal/agentmail` and fails closed without `AGENTMAIL_WEBHOOK_SECRET`.
- Register the webhook with `scripts/register_agentmail_webhook.sh`.
- The agent gateway lives in `openleg-ops`.

## Data Policy

Citizen smart meter data stays within each LEG.  
Data is not sold and not aggregated for third parties.
