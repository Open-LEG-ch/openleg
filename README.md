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

### From meter reading to invoice

The path from raw metering data to a member invoice runs as one workflow in this repository:

1. **Import.** `scripts/fetch_sdat.py` collects SDAT files from the Swisseldex Datahub, `scripts/import_sdat.py` parses ebIX E66 documents into `store/metering.py`; `sdat_datahub.py` and `sdat_e66.py` hold the transport and parsing logic, `scripts/sdat_pipeline.sh` chains the steps. `scripts/import_metering_points.py` maps metering points to buildings.
2. **Allocation.** `billing_readings.py` turns point-keyed readings into participant frames and refuses a period it cannot bill. `billing_engine.py` allocates the quarter hours and produces an auditable draft with positive `consumer_charge` items and negative `producer_credit` items; `store/billing.py` persists period, price snapshot, and signed line items. `billing_runner.py` orchestrates one period, and `POST /api/cron/process-billing` in `cron.py` runs the previous complete month for every active community.
3. **Policy and approval.** The operator maintains a versioned tariff at `/leg/community/<community_id>/billing-policy` (`billing_policy.py`, `templates/leg_billing_policy.html`) and reviews the draft at `/leg/community/<community_id>/billing` (`templates/leg_billing.html`). Approval freezes one immutable invoice per participant from the persisted policy snapshot (`billing_approval.py`).
4. **Lifecycle and delivery.** An issued invoice moves through `issued`, `delivered`, `paid`, `cancelled`, and `corrected` (`billing_lifecycle.py`). Members read their own invoices at `/dashboard/invoices`, `/dashboard/invoices/<invoice_id>`, and `/dashboard/invoices/<invoice_id>/pdf` (`member_invoices.py`, `templates/member_invoices.html`, `templates/member_invoice_detail.html`).

What still needs a human or a shell: the SDAT fetch and import run as command line scripts, no cron route triggers them; approval stays a deliberate operator action; payment has no bank reconciliation, so an operator marks an invoice paid.

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

New to the codebase? Follow the [contributor onboarding guide](docs/contributor-onboarding.md).

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

### Vom Zählerwert zur Rechnung

Der Weg von Rohmessdaten zur Mitgliederrechnung läuft als ein Ablauf in diesem Repo:

1. **Import.** `scripts/fetch_sdat.py` holt SDAT-Dateien vom Swisseldex Datahub, `scripts/import_sdat.py` liest ebIX-E66-Dokumente nach `store/metering.py`; `sdat_datahub.py` und `sdat_e66.py` enthalten Transport und Parsing, `scripts/sdat_pipeline.sh` verkettet die Schritte. `scripts/import_metering_points.py` ordnet Messpunkte den Gebäuden zu.
2. **Verteilung.** `billing_readings.py` formt messpunktbezogene Werte in Teilnehmerreihen um und weist eine Periode zurück, die es nicht abrechnen kann. `billing_engine.py` verteilt die Viertelstunden und erzeugt einen prüfbaren Entwurf mit positiven `consumer_charge` Positionen und negativen `producer_credit` Positionen; `store/billing.py` speichert Periode, Preisstand und Positionen mit Vorzeichen. `billing_runner.py` steuert einen Lauf, `POST /api/cron/process-billing` in `cron.py` rechnet den letzten vollen Monat für jede aktive Gemeinschaft.
3. **Policy und Freigabe.** Die Betreiberin pflegt den versionierten Tarif unter `/leg/community/<community_id>/billing-policy` (`billing_policy.py`, `templates/leg_billing_policy.html`) und prüft den Entwurf unter `/leg/community/<community_id>/billing` (`templates/leg_billing.html`). Die Freigabe friert je Teilnehmer eine unveränderliche Rechnung aus dem gespeicherten Policy-Snapshot ein (`billing_approval.py`).
4. **Lebenszyklus und Zustellung.** Eine freigegebene Rechnung durchläuft `issued`, `delivered`, `paid`, `cancelled` und `corrected` (`billing_lifecycle.py`). Mitglieder lesen ihre Rechnungen unter `/dashboard/invoices`, `/dashboard/invoices/<invoice_id>` und `/dashboard/invoices/<invoice_id>/pdf` (`member_invoices.py`, `templates/member_invoices.html`, `templates/member_invoice_detail.html`).

Das bleibt Handarbeit oder Shell: Abruf und Import der SDAT-Dateien starten über Kommandozeilenskripte, kein Cron-Endpunkt löst sie aus; die Freigabe bleibt eine bewusste Entscheidung der Betreiberin; für Zahlungen gibt es keinen Bankabgleich, eine Person setzt die Rechnung auf bezahlt.

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

Neu im Code? Folgen Sie der [Anleitung für Mitwirkende](docs/contributor-onboarding.md).

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
