# Contributing to OpenLEG

Willkommen! Welcome! Bienvenue! Benvenuti!

OpenLEG is free, open-source infrastructure for Swiss Lokale Elektrizitätsgemeinschaften (LEG). Contributions from developers, energy experts, and municipal stakeholders are welcome.

## Dev Setup

```bash
git clone https://github.com/wgusta/openleg.git
cd openleg
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # configure DATABASE_URL
pytest tests/ -v
```

Local dev server: `python app.py` (runs on :5003).

## Architecture

Flask routes (`app.py`) serve the web UI and API. PostgreSQL (`database.py`, 23+ tables) stores all data. Caddy handles reverse proxy and TLS. Multi-tenant resolution via `tenant.py` maps `<city>.openleg.ch` subdomains to territory configs in `white_label_configs`.

Key modules:
- `public_data.py`: ElCom SPARQL, Energie Reporter, Sonnendach fetchers
- `api_public.py`: Public REST API (`/api/v1/*`), no auth, CORS
- `formation_wizard.py`: LEG formation flow, financial model
- `municipality.py`: Municipality onboarding, profil pages
- `email_automation.py`: SMTP drip campaigns
- `tenant.py`: Multi-tenant hostname resolution

## Test Patterns

Tests use `conftest.py` fixtures: `mock_db` (mocked PostgreSQL), `full_client` (full Flask app with mocked DB). TDD red-green-refactor cycle. Each feature gets its own test file in `tests/`.

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_municipality_seeding.py -v

# Run single test
pytest tests/test_municipality_seeding.py::TestSeedAllMunicipalities::test_seeds_municipalities -v
```

## Code Style

- German UI copy, active voice verbs (Aktiv, not Passiv)
- No over-engineering: minimum code for current requirements
- No emojis in code or UI
- Avoid em dashes; use commas, colons, semicolons
- Type hints encouraged but not mandatory
- Docstrings for public functions

## Data Contributions

Municipality data comes from public Swiss sources (ElCom, Energie Reporter, Sonnendach). Contributions welcome:
- Tariff updates for new operators
- Energie Reporter coverage improvements
- PLZ range corrections per kanton
- DSO contact information

All public data, no PII. Citizen smart meter data stays within their LEG.

## Good First Issues

1. **Add PLZ ranges for a new kanton**: extend kanton config in `tenant.py`
2. **New email template for lifecycle stage**: add template in `templates/emails/`, wire in `email_automation.py`
3. **Improve Sonnendach data coverage**: enhance `public_data.py` fetcher for missing municipalities

## License

AGPL-3.0. All contributions are licensed under the same terms.
