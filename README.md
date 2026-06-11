# OpenLEG

OpenLEG is open-source infrastructure for Swiss Local Electricity Communities (LEG). It is the public app repo: runtime code, tests, templates, docs, and CI live here. Private operations, deployment runbooks, and internal planning stay in a separate private repository.

## What this repo is

- Flask app and API code
- Database layer and migrations
- Templates and static assets
- Public documentation and contribution workflow
- CI and test automation

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

## Public architecture

- Backend: Flask on Python 3.11
- Database: PostgreSQL 16
- Cache: Redis 7
- Reverse proxy: Caddy

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
