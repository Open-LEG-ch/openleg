# OpenLEG

OpenLEG is open-source infrastructure for Swiss Local Electricity Communities (LEG). It is the public app repo: runtime code, tests, templates, docs, and CI live here. Private operations, deployment runbooks, and internal planning stay in a separate private repository.

## What this repo is

- Flask app and API code
- Database layer and migrations
- Templates and static assets
- Public documentation and contribution workflow
- CI and test automation

## Choose your path

OpenLEG serves four audiences. Pick the one that matches you.

| You are | On the site | Get started |
| --- | --- | --- |
| Resident / founder | `/fuer-bewohner` | Check your address, find neighbours, start a LEG on the hosted platform. |
| LEG operator | `/leg-gruenden` | Found and run a community; manage members and self-consumption billing. |
| Municipality | `/fuer-gemeinden` | Compare solar usage, claim a free `<gemeinde>.openleg.ch` page. |
| Developer / self-host | this repo | Read the code, use the free API, run your own instance (below). |

Most users do not self-host: the hosted platform gives every municipality and
LEG its own subdomain at no cost. Self-hosting is for teams who want full data
sovereignty on their own infrastructure.

### Live examples

- Municipality: `/gemeinde/profil/4021` (Baden, real ElCom and Sonnendach data)
- LEG: `/leg-gruenden` walks a concrete formation end to end

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
pytest tests/ -q
python app.py
```

## Docker

```bash
cp .env.example .env
docker compose up -d
docker compose ps
```

## OpenClaw

- `openclaw/` contains a public-safe OpenClaw bundle for local automation against OpenLEG.
- It is separate from the public `docker-compose.yml`.
- Start with [openclaw/README.md](openclaw/README.md).

## Public architecture

- Backend: Flask on Python 3.11
- Database: PostgreSQL 16
- Cache: Redis 7
- Reverse proxy: Caddy

See:

- [Architecture](docs/architecture.md)
- [Data pipeline](docs/data-pipeline.md)
- [API examples](docs/api-examples.md)

## Route map

- `/` resident and municipality entry point
- `/rangliste` and `/gemeinde/profil/<bfs>` solar utilization ranking
- `/fuer-gemeinden` municipality onboarding overview
- `/open-source` codebase and self-hosting explainer
- `/api/v1/docs` public API documentation
- `/health` and `/livez` runtime health checks

## Data pipeline

- Public data fetchers live in `public_data.py`
- Persistent tables and migrations live in `database.py`
- PV ranking import lives in `scripts/load_pv_data.py`
- API read paths live in `api_public.py` and ranking blueprints

## Contributing

- Open an issue before larger changes
- Keep changes small and covered by tests
- Run `pytest tests/ -q`, `ruff check .`, and `ruff format --check .` before opening a PR
- Target the required CI checks: `ci/lint`, `ci/test`, `ci/security`

## Repository boundary

- Public code stays in this repo
- Secrets stay out of git
- Production host inventory stays private
- Internal strategy, grant work, and operational notes stay in the private ops repository

## Security

- Never commit production credentials or personal data
- Use `.env.example` as the local template
- Report security issues through the repository security workflow

## License

AGPL-3.0-or-later
