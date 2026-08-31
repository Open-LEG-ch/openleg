# OpenLEG

[English](#english) | [Deutsch](#deutsch)

Open-source infrastructure for Swiss Local Electricity Communities. Offene Infrastruktur für Schweizer Lokale Elektrizitätsgemeinschaften.

## English

OpenLEG is the public website, product application, and API for founding and operating a Swiss Local Electricity Community, known as a LEG. The private `openleg-ops` repository owns production deployment, not the public site runtime or assets.

### What this repo is

- `app.py` connects the Flask routes and user journeys
- `api_public.py` provides the public JSON API
- `database.py` owns connections and migrations; `store/` contains domain repositories
- `billing_engine.py` contains quarter-hour allocation and draft billing logic
- `templates/`, `static/`, and `tests/` contain the interface and its checks

### Choose your path

| You are | Start here | What you can do |
| --- | --- | --- |
| Owner or founder | `/dashboard` | Open the owner dashboard and organise a LEG. |
| LEG operator | `/leg/dashboard` | Manage members, contracts, metering, and billing. |
| Municipality | `/gemeinde/dashboard` | Open the municipality dashboard. |
| Developer or self-hoster | `/api/v1/docs` | Use the API or run your own instance. |

### Current billing boundary

`billing_engine.py` accepts participant-keyed 15-minute consumption and production frames plus explicit prices. It creates an auditable draft with positive `consumer_charge` items and negative `producer_credit` items. `store/billing.py` persists the period, price snapshot, and signed line items.

Metering import, member mapping, a CLI command to start a billing run, and final invoice or credit documents are not one automated public workflow yet. The repository contains the reviewed billing core, not a promise that raw meter data can already produce final documents without integration work.

### Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
pytest tests/ -q
python app.py
```

The dev server listens on the port from `APP_BASE_URL` in `.env` (default 5003).

For self-hosting:

```bash
curl -fsSL https://openleg.ch/install.sh | bash
```

Or use Docker directly:

```bash
cp .env.example .env
docker compose up -d
docker compose ps
```

### Public architecture

- Backend: Flask on Python 3.11
- Database: PostgreSQL 16
- Cache: Redis 7
- Reverse proxy: Caddy

Read the [engineering contract](docs/engineering-contract.md), [architecture](docs/architecture.md), [data pipeline](docs/data-pipeline.md), [dashboard access](docs/dashboard-access.md), and [API examples](docs/api-examples.md).

### Route map

- `/` public website for anonymous visitors
- `/login` role chooser for owners and municipalities
- `/dashboard` owner dashboard and secure access request
- `/leg/dashboard` LEG operator dashboard
- `/gemeinde/dashboard` municipality dashboard and secure access request
- `/utility/login` grid-operator login
- `/api/v1/docs` public API documentation
- `/health` and `/livez` runtime checks

### Data pipeline

Public data fetchers live in `public_data.py`. Database persistence uses `database.py` and `store/`. The PV ranking import is `scripts/load_pv_data.py`; SDAT retrieval from the Swisseldex Datahub lives in `sdat_datahub.py` and `scripts/fetch_sdat.py`. API read paths live in `api_public.py` and the ranking blueprints.

### Contributing

Open an issue before a larger change. Keep the pull request small, start with a failing test, and run:

```bash
scripts/tdd_cycle.sh gate
```

Required checks are `ci/lint`, `ci/test`, and `ci/security`.

### Repository boundary

Public product code belongs here. Production credentials, citizen data, host inventory, deployment runbooks, and internal planning stay outside this repository. The public repo publishes container images; it never deploys production.

### Security

Never commit credentials or personal data. Use `.env.example` locally and report vulnerabilities through the repository security workflow.

## Deutsch

OpenLEG ist die öffentliche Website, Produktanwendung und API für die Gründung und den Betrieb einer Schweizer Lokalen Elektrizitätsgemeinschaft, kurz LEG. Das private Repo `openleg-ops` verantwortet die Produktionsbereitstellung, nicht die öffentliche Website oder ihre Assets.

### Was dieses Repo enthält

- `app.py` verbindet Flask-Routen und Nutzerwege
- `api_public.py` stellt die öffentliche JSON-API bereit
- `database.py` besitzt Verbindungen und Migrationen; `store/` enthält die fachlichen Repositories
- `billing_engine.py` enthält Logik für Viertelstundenverteilung und Abrechnungsentwürfe
- `templates/`, `static/` und `tests/` enthalten Oberfläche und Prüfungen

### Wählen Sie Ihren Einstieg

| Sie sind | Einstieg | Das können Sie tun |
| --- | --- | --- |
| Eigentümer oder Gründer | `/dashboard` | Eigentümer-Dashboard öffnen und eine LEG organisieren. |
| LEG-Betreiber | `/leg/dashboard` | Mitglieder, Verträge, Messung und Abrechnung verwalten. |
| Gemeinde | `/gemeinde/dashboard` | Gemeinde-Dashboard öffnen. |
| Entwickler oder Selbsthoster | `/api/v1/docs` | API nutzen oder eigene Instanz betreiben. |

### Aktuelle Abrechnungsgrenze

`billing_engine.py` übernimmt nach Teilnehmern geordnete Viertelstundenwerte für Bezug und Einspeisung sowie explizite Preise. Daraus entsteht ein prüfbarer Entwurf mit positiven `consumer_charge` Positionen und negativen `producer_credit` Positionen. `store/billing.py` speichert Periode, Preisstand und Positionen mit Vorzeichen.

Messdatenimport, Mitgliederzuordnung, ein CLI-Befehl für den Abrechnungslauf und definitive Rechnungs- oder Gutschriftdokumente bilden noch keinen automatisierten öffentlichen Ablauf. Das Repo enthält den geprüften Abrechnungskern. Rohdaten allein erzeugen ohne Integrationsarbeit noch keine definitiven Dokumente.

### Schnellstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
pytest tests/ -q
python app.py
```

Der Entwicklungsserver läuft auf dem Port aus `APP_BASE_URL` in `.env` (Standard 5003).

Für den Eigenbetrieb:

```bash
curl -fsSL https://openleg.ch/install.sh | bash
```

Oder direkt mit Docker:

```bash
cp .env.example .env
docker compose up -d
docker compose ps
```

### Öffentliche Architektur

- Backend: Flask auf Python 3.11
- Datenbank: PostgreSQL 16
- Cache: Redis 7
- Reverse Proxy: Caddy

Lesen Sie den [Entwicklungsvertrag](docs/engineering-contract.md), die [Architektur](docs/architecture.md), die [Datenpipeline](docs/data-pipeline.md), den [Dashboard-Zugriff](docs/dashboard-access.md) und die [API-Beispiele](docs/api-examples.md).

### Routen

- `/` öffentliche Website für nicht angemeldete Personen
- `/login` Rollenwahl für Eigentümer und Gemeinden
- `/dashboard` Eigentümer-Dashboard mit sicherer Zugangsanfrage
- `/leg/dashboard` Dashboard für LEG-Betreiber
- `/gemeinde/dashboard` Gemeinde-Dashboard mit sicherer Zugangsanfrage
- `/utility/login` Login für Netzbetreiber
- `/api/v1/docs` öffentliche API-Dokumentation
- `/health` und `/livez` Laufzeitprüfungen

### Datenpipeline

`public_data.py` lädt öffentliche Daten. `database.py` und `store/` speichern sie. `scripts/load_pv_data.py` importiert die PV-Rangliste; `sdat_datahub.py` und `scripts/fetch_sdat.py` holen SDAT-Dateien vom Swisseldex Datahub. `api_public.py` und die Ranglisten-Blueprints liefern die Daten aus.

### Mitwirken

Eröffnen Sie vor einer grösseren Änderung ein Issue. Halten Sie den Pull Request klein, beginnen Sie mit einem fehlschlagenden Test und führen Sie danach aus:

```bash
scripts/tdd_cycle.sh gate
```

Erforderlich sind `ci/lint`, `ci/test` und `ci/security`.

### Repo-Grenze

Öffentlicher Produktcode gehört hierher. Produktive Zugangsdaten, Bürgerdaten, Host-Inventar, Deployment-Runbooks und interne Planung bleiben ausserhalb dieses Repos. Das öffentliche Repo publiziert Container-Images, deployt aber nie die Produktion.

### Sicherheit

Committen Sie nie Zugangsdaten oder Personendaten. Nutzen Sie lokal `.env.example` und melden Sie Schwachstellen über den Security-Prozess des Repos.

## License / Lizenz

AGPL-3.0-or-later
