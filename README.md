# OpenLEG

## Deutsch

### Übersicht
OpenLEG ist eine quelloffene Plattform zur Unterstützung von Lokalen Elektrizitätsgemeinschaften (LEG) in der Schweiz.  
Der öffentliche Repository-Inhalt ist auf Produktbetrieb, Entwicklung und Deployment ausgerichtet.

### Architektur
- Backend: Flask (Python 3.11)
- Datenbank: PostgreSQL 16
- Caching: Redis 7
- Reverse Proxy: Caddy
- Zusatzdienst: OpenClaw Gateway

### Lokale Entwicklung
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
python app.py
```

### Docker-Betrieb
```bash
cp .env.example .env
docker compose up -d
```

### Deployment
- Verwende das öffentliche Template-Skript: `deploy.example.sh`
- Setze Ziel-Host und Zielpfad per Umgebungsvariablen
- Führe zuerst einen Dry-Run aus

Beispiel:
```bash
DEPLOY_HOST=ubuntu@1.2.3.4 \
REMOTE_DIR=/opt/openleg \
./deploy.example.sh --dry-run
```

### Wichtige Umgebungsvariablen
- `POSTGRES_PASSWORD`
- `SECRET_KEY`
- `ADMIN_TOKEN`
- `CRON_SECRET`
- `SMTP_*` (falls E-Mail aktiv)
- `GROQ_API_KEY`, `OPENCLAW_GATEWAY_TOKEN`, `OPENCLAW_GATEWAY_PASSWORD` (für OpenClaw)

### Troubleshooting
- Containerstatus prüfen: `docker compose ps`
- Logs prüfen: `docker compose logs -f flask`
- Health prüfen: `curl -fsS http://localhost:5000/livez`
- Tests ausführen: `pytest tests/ -q`

### Sicherheit
- Keine produktiven Secrets im Repository committen
- Lokale/private Betriebsdokumente bleiben untracked
- Nutze `.gitignore` für interne oder unsichere Artefakte

## English

### Overview
OpenLEG is an open-source platform supporting Local Electricity Communities (LEG) in Switzerland.  
This public repository is focused on product runtime, development, and deployment.

### Architecture
- Backend: Flask (Python 3.11)
- Database: PostgreSQL 16
- Cache: Redis 7
- Reverse proxy: Caddy
- Sidecar service: OpenClaw Gateway

### Local Development
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
python app.py
```

### Docker Runtime
```bash
cp .env.example .env
docker compose up -d
```

### Deployment
- Use the public template script: `deploy.example.sh`
- Set target host/path through environment variables
- Run dry-run first

Example:
```bash
DEPLOY_HOST=ubuntu@1.2.3.4 \
REMOTE_DIR=/opt/openleg \
./deploy.example.sh --dry-run
```

### Important Environment Variables
- `POSTGRES_PASSWORD`
- `SECRET_KEY`
- `ADMIN_TOKEN`
- `CRON_SECRET`
- `SMTP_*` (if email enabled)
- `GROQ_API_KEY`, `OPENCLAW_GATEWAY_TOKEN`, `OPENCLAW_GATEWAY_PASSWORD` (for OpenClaw)

### Troubleshooting
- Check containers: `docker compose ps`
- Check logs: `docker compose logs -f flask`
- Check health: `curl -fsS http://localhost:5000/livez`
- Run tests: `pytest tests/ -q`

### Security
- Never commit production secrets
- Keep private operational docs untracked
- Use `.gitignore` for internal or uncertain artifacts
