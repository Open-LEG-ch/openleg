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
- AI gateway: OpenClaw
- Domains: `openleg.ch`, `<city>.openleg.ch`, `api.openleg.ch`, `claw.openleg.ch`

Domain vocabulary lives in `CONTEXT.md`. Use those module/seam names.

## Data Layer

- `database.py` owns the connection seam (`get_connection`) plus schema/migrations.
- Self-contained domains are extracted into per-domain repositories under `store/`
  (first: `store/ranking.py` for PV/ranking storage). Each repository resolves the
  seam via `database.get_connection` and is re-exported from `database.py`, so
  `import database as db; db.<fn>()` keeps working unchanged.
- New storage code for a cohesive domain goes in `store/`, not into `database.py`.
- Deepening roadmap and next extraction order: `prd/architecture-deepening.md`.

## Domain Modules (Candidate 2, shipped)

All three Architecture Deepening candidates are complete:

- `ranking.py` — Ranking facade (#1). Wraps `pv_ranking`/`pv_badge`; call `Ranking.load()` once per request path.
- `cantons.py` — `SWISS_CANTON_OPTIONS` / `SWISS_CANTONS` (extracted from route module).
- `municipality_profile.py` — `public_profile(bfs)` verb (#2, slice 1). Assembles profile page data; route in `municipality.py` is now parse+render only.
- `registration.py` — `check_potential`, `register_anonymous`, `register_full` verbs (#2, slices 2-3). Owns `parse_consents`, `collect_building_locations`, `jitter_coordinates`, `find_provisional_matches`, `send_confirmation_email`, `run_full_ml_task`.
- `dashboard.py` — `readiness(building_id, *, city_id, app_base_url)` verb (#2, slice 4). Route in `app.py` is parse+render only.
- `templates/base.html` — shared layout seam (#3). All user-facing pages use `{% extends "base.html" %}`.

Route handlers in `app.py` and `municipality.py` contain no direct `db.*` calls for the above paths.

## Templates and Pathways

- Every user-facing page uses the shared partials `partials/tailwind_brand.html`
  (built CSS, never the Tailwind CDN), `partials/site_nav.html`, `partials/site_footer.html`.
- Tailwind is compiled from `static/css/tailwind.css` to `static/css/openleg.css`
  (`npx tailwindcss -i static/css/tailwind.css -o static/css/openleg.css --minify`);
  rebuild after adding new utility classes.
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

Production deployment procedures are documented in `openleg-ops`.

## VPS Access Rules (Public-Safe)

- Host: `83.228.223.66`
- SSH user: `ubuntu`
- SSH identity file: `~/.ssh/infomaniak_badenleg`
- Remote deployment dir: `/opt/badenleg`
- Containers: `openleg-flask`, `openleg-openclaw`, `openleg-caddy`, `openleg-postgres`, `openleg-redis`
- Safe verification commands:
  - `docker ps`
  - `docker exec openleg-openclaw openclaw mcp list`
  - `curl -f https://openleg.ch/`
  - `curl -f https://claw.openleg.ch/`
- Secret-bearing procedures and `.env` edits belong in `openleg-ops`.
- Env checks must report presence only, never values.

## CI Notes

- **Node.js 24 migration (2026-06-16)**: GitHub Actions forces Node.js 24 as default from June 16, 2026. `actions/checkout@v4` and `actions/setup-python@v5` (both pinned by SHA in `.github/workflows/`) should be compatible with Node 24 as maintained by GitHub. Monitor CI after June 16; if jobs fail, bump the `@v4`/`@v5` SHAs to the latest release.
- **numpy upgrade blocked by scipy**: numpy 2.x requires pandas 3.x AND scipy>=1.13. Current scipy==1.11.4 caps numpy<1.28. To upgrade numpy 2.x: bump scipy to 1.13+ first, then numpy. Merge order: pandas 3.x → scikit-learn → scipy 1.13+ → numpy 2.x.

## Current Blocker

- LEA AgentMail and OpenClaw wiring is implemented in repo. A working build is on branch `fix/openclaw-stable-agentmail`; full production cutover still needs private-ops `docker-compose.yml` overrides and AgentMail webhook registration.
- Required next-step secrets/config in `openleg-ops`: `AGENTMAIL_API_KEY`, `AGENTMAIL_WEBHOOK_SECRET`, `INTERNAL_TOKEN`, `APP_BASE_URL`, VPS service/runtime config.
- Required next-step actions after secrets exist: register AgentMail webhook via `openclaw/config/cron/register_agentmail_webhook.sh`, rebuild/restart OpenClaw container, verify `/api/internal/agentmail` and `/admin/ops`.

## OpenClaw Deployment Notes

Learnings from building and running OpenClaw on the VPS. Host-specific steps belong in `openleg-ops`; this section only covers the public-repo bits.

### Image build

- OpenClaw `2026.4.5` has packaging quirks: installing from scratch with `npm`/`pnpm` misses optional native/channel deps (`@buape/carbon`, `@larksuiteoapi/node-sdk`, etc.). The current Dockerfile uses a known-working local rollback image (`openleg-openclaw:rollback`) as base and overlays the current MCP server + config. If you rebuild the base image from scratch, expect to chase missing modules.
- The MCP server is installed with `npm install --production` inside the container on top of the rollback base.

### Config schema

- The OpenClaw build in use expects MCP servers under `mcp.servers`, **not** top-level `mcpServers`. `mcpServers` is rejected as an unrecognized key.
- The tracked template is `openclaw/config/openclaw.example.json`. The runtime `openclaw/config/openclaw.json` is gitignored, so copy/adapt the example on each host.

### Gateway binding

- The entrypoint starts the gateway with `--bind "$OPENCLAW_GATEWAY_BIND"`, defaulting to `loopback`. That makes the container unreachable from Caddy on the Docker bridge.
- Set `OPENCLAW_GATEWAY_BIND=lan` in the compose environment, or use `bind: "custom"` + `customBindHost: "0.0.0.0"` in config, to listen on all interfaces.

### Reverse proxy

- Caddy proxies `claw.openleg.ch` to the Docker service name `openclaw:18789`. The gateway and Caddy must share the same `web` network.

### AgentMail MCP env

- The `openleg` MCP server needs these env vars passed into the OpenClaw container:
  `DATABASE_URL`, `INTERNAL_TOKEN`, `AGENTMAIL_API_KEY`, `AGENTMAIL_WEBHOOK_SECRET`,
  `AGENTMAIL_API_BASE`, `LEA_INBOX_ADDRESS`, `AGENTMAIL_HUMAN_EMAIL`, `LEA_AGENT_ID`,
  `FLASK_URL`, `APP_BASE_URL`, `BRAVE_API_KEY`, `OPENCLAW_READONLY`.

### Verification

- `docker exec openleg-openclaw openclaw mcp list` should show `openleg`.
- `docker exec openleg-openclaw openclaw dashboard` prints the Control UI URL with a fresh token.
- `curl -f https://claw.openleg.ch/` should return 200.

### Known non-fatal warnings

- `failed to persist plugin auto-enable changes: EBUSY ... openclaw.json` happens when `openclaw.json` is mounted read-only. Startup succeeds; remove `:ro` if you want OpenClaw to persist runtime config changes.
- `gateway.controlUi.dangerouslyAllowHostHeaderOriginFallback=true` weakens origin checks. It is enabled as break-glass for the Caddy reverse proxy; review when a proper trusted-proxy setup is available.

## Data Policy

Citizen smart meter data stays within each LEG.  
Data is not sold and not aggregated for third parties.
