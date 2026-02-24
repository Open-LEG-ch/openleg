# OpenLEG

Open-source Swiss energy data API + LEG intelligence platform for Swiss municipalities.

## Features

- Multi-tenant municipality onboarding (B2G)
- Smart meter data collection and analysis (B2C)
- Aggregated intelligence API for energy providers (B2B)
- Public data API (ElCom tariffs, Sonnendach, Energie Reporter)
- LEG formation wizard with financial modeling
- DBSCAN clustering for community matching
- OpenClaw AI assistant (LEA) with direct DB access

## Tech Stack

- **Backend**: Flask, Python 3.11, Gunicorn
- **Database**: PostgreSQL 16
- **Frontend**: HTML, TailwindCSS, Leaflet.js
- **ML**: scikit-learn (DBSCAN), scipy, numpy, pandas
- **AI**: OpenClaw gateway with MCP server
- **Infra**: Docker Compose, Caddy (auto TLS), Infomaniak VPS

## Architecture

```
Infomaniak VPS (83.228.223.66)
┌──────────────────────────────────────┐
│  Caddy (:80/:443)                    │
│    openleg.ch → flask:5000           │
│    openclaw.openleg.ch → oclaw:18789 │
│                                      │
│  flask (gunicorn, 3 workers)         │
│  postgres:16-alpine (local volume)   │
│  openclaw + mcp-openleg-server       │
└──────────────────────────────────────┘
```

## Quick Start

### Local Development

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in values
python app.py          # runs on :5003
```

### Production (Docker)

```bash
cp .env.example .env   # fill in real secrets
docker compose up -d
```

See [DEPLOYMENT.md](DEPLOYMENT.md) for full VPS deployment instructions.

## License

[To be determined]

**OpenLEG** by Sihl Icon Valley @ sihliconvalley.ch
