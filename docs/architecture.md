# Architecture

OpenLEG is a Flask application for Swiss Lokale Elektrizitätsgemeinschaften.
This public repository contains product runtime code, tests, templates, public
docs, and CI. Production operations and secret handling belong outside this
repository.

## Runtime

- Flask serves HTML pages, forms, health checks, and JSON API routes.
- PostgreSQL stores registrations, municipality profiles, tariffs, PV ranking
  data, consent records, and operational app state.
- Redis backs rate limiting and short lived cache state.
- Caddy terminates TLS and proxies traffic to the Flask container.

## Route map

- `/` resident and municipality entry point with address check.
- `/how-it-works` explains the resident LEG path.
- `/fuer-gemeinden` explains self-hosting and hosted municipality onboarding.
- `/open-source` explains the public app repo and private ops boundary.
- `/rangliste` lists solar utilization by municipality.
- `/rangliste/fortschritte` shows the strongest PV movers.
- `/rangliste/vergleich` compares two municipalities.
- `/gemeinde/profil/<bfs>` renders one municipality profile.
- `/api/v1/docs` documents the public API.
- `/robots.txt` and `/sitemap.xml` support indexing.
- `/health` and `/livez` support runtime checks.

## Code Map

- `app.py`: Flask app factory, security policy, public HTML routes, cron routes.
- `api_public.py`: unauthenticated public JSON API.
- `database.py`: schema setup, migrations, and query helpers.
- `public_data.py`: open data fetchers for ElCom, Energie Reporter, Sonnendach.
- `pv_ranking.py`: solar utilization ranking logic.
- `rangliste.py`: ranking pages and SVG share assets.
- `municipality.py`: municipality directory and profile pages.
- `templates/`: Jinja pages and shared partials.
- `static/`: CSS, images, and browser JavaScript.
- `tests/`: regression, security, API, docs, and route contract tests.

## Request Flow

1. Caddy receives HTTPS traffic and forwards to Flask.
2. Flask applies host, tenant, rate limit, and security middleware.
3. Route handlers read public data through `database.py`.
4. Templates render HTML with public-safe metadata and links.
5. API routes return JSON from stable read models.

## Contribution Boundaries

- Keep product code, public docs, tests, fixtures, and examples here.
- Do not add credentials, host inventory, incident runbooks, or internal plans.
- Prefer focused tests before implementation.
- Run `pytest tests/ -q`, `ruff check .`, and `ruff format --check .`.
