# OpenLEG

[English](#english) | [Deutsch](#deutsch)

Open-source infrastructure for Swiss Local Electricity Communities. Offene Infrastruktur für Schweizer Lokale Elektrizitätsgemeinschaften.

## English

OpenLEG is the public application for founding and operating a Swiss Local Electricity Community, known as a LEG. Runtime code, tests, database migrations, templates, and public documentation live here. Production secrets and host-specific operations do not.

### What this repo is

- `app.py` connects the Flask routes and user journeys
- `api_public.py` provides the public JSON API
- `database.py` owns connections and migrations; `store/` contains domain repositories
- `billing_engine.py` contains quarter-hour allocation and draft billing logic
- `templates/`, `static/`, and `tests/` contain the interface and its checks

### Choose your path

| You are | Start here | What you can do |
| --- | --- | --- |
| Resident or founder | `/fuer-bewohner` | Check an address, find neighbours, start a LEG. |
| LEG operator | `/leg-gruenden` | Organise members, contracts, metering, and billing. |
| Municipality | `/fuer-gemeinden` | Review local solar use and create a municipality page. |
| Developer or self-hoster | `/open-source` | Trace the code, use the API, or run your own instance. |

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
- Local automation: the public-safe bundle in [openclaw/README.md](openclaw/README.md), separate from the default `docker-compose.yml`

Read [Architecture](docs/architecture.md), [Data pipeline](docs/data-pipeline.md), and [API examples](docs/api-examples.md).

### Route map

- `/` entry point for residents and municipalities
- `/rangliste` solar utilization ranking
- `/gemeinde/profil/4021` municipality profile example
- `/open-source` technical pathway and self-hosting context
- `/self-host` self-hosting guide
- `/api/v1/docs` public API documentation
- `/health` and `/livez` runtime checks

### Data pipeline

Public data fetchers live in `public_data.py`. Database persistence uses `database.py` and `store/`. The PV ranking import is `scripts/load_pv_data.py`; API read paths live in `api_public.py` and the ranking blueprints.

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

OpenLEG ist die öffentliche Anwendung für die Gründung und den Betrieb einer Schweizer Lokalen Elektrizitätsgemeinschaft, kurz LEG. Laufzeitcode, Tests, Datenbankmigrationen, Templates und öffentliche Dokumentation liegen hier. Produktive Secrets und hostspezifischer Betrieb gehören nicht in dieses Repo.

### Was dieses Repo enthält

- `app.py` verbindet Flask-Routen und Nutzerwege
- `api_public.py` stellt die öffentliche JSON-API bereit
- `database.py` besitzt Verbindungen und Migrationen; `store/` enthält die fachlichen Repositories
- `billing_engine.py` enthält Logik für Viertelstundenverteilung und Abrechnungsentwürfe
- `templates/`, `static/` und `tests/` enthalten Oberfläche und Prüfungen

### Wählen Sie Ihren Einstieg

| Sie sind | Einstieg | Das können Sie tun |
| --- | --- | --- |
| Bewohner oder Gründer | `/fuer-bewohner` | Adresse prüfen, Nachbarn finden, LEG starten. |
| LEG-Betreiber | `/leg-gruenden` | Mitglieder, Verträge, Messung und Abrechnung organisieren. |
| Gemeinde | `/fuer-gemeinden` | Lokale Solarnutzung prüfen und Gemeindeseite erstellen. |
| Entwickler oder Selbsthoster | `/open-source` | Code verfolgen, API nutzen oder eigene Instanz betreiben. |

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
- Lokale Automation: öffentliches Paket in [openclaw/README.md](openclaw/README.md), getrennt vom normalen `docker-compose.yml`

Lesen Sie [Architektur](docs/architecture.md), [Datenpipeline](docs/data-pipeline.md) und [API-Beispiele](docs/api-examples.md).

### Routen

- `/` Einstieg für Bewohner und Gemeinden
- `/rangliste` Rangliste zur Solarnutzung
- `/gemeinde/profil/4021` Beispiel für ein Gemeindeprofil
- `/open-source` technischer Einstieg und Kontext zum Eigenbetrieb
- `/self-host` Anleitung für den Eigenbetrieb
- `/api/v1/docs` öffentliche API-Dokumentation
- `/health` und `/livez` Laufzeitprüfungen

### Datenpipeline

`public_data.py` lädt öffentliche Daten. `database.py` und `store/` speichern sie. `scripts/load_pv_data.py` importiert die PV-Rangliste; `api_public.py` und die Ranglisten-Blueprints liefern die Daten aus.

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
