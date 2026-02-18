# BadenLEG

Web platform helping Baden (CH) residents form Local Electricity Communities (Lokale Elektrizitätsgemeinschaft) starting January 2026.

## Features

- Address matching with DBSCAN clustering to find LEG partners
- Interactive map with anonymized building locations (120m jitter)
- Email verification (two step confirmation via SendGrid)
- Formation wizard for community setup
- Educational content: LEG, EVL/vEVL, ZEV/vZEV comparison
- Admin dashboard with analytics
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
│    badenleg.ch → flask:5000          │
│    openclaw.badenleg.ch → oclaw:18789│
│                                      │
│  flask (gunicorn, 3 workers)         │
│  postgres:16-alpine (local volume)   │
│  openclaw + mcp-badenleg-server      │
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

## Project Structure

```
├── app.py                 # Main Flask app, all routes
├── database.py            # PostgreSQL layer (14 tables, auto-created)
├── formation_wizard.py    # LEG formation flow
├── ml_models.py           # DBSCAN clustering
├── email_automation.py    # SendGrid drip campaigns
├── security_utils.py      # Input validation, sanitization
├── data_enricher.py       # Geocoding, energy profiles
├── token_persistence.py   # JSON fallback token storage
├── Dockerfile             # Flask app container
├── docker-compose.yml     # All services
├── Caddyfile              # Reverse proxy config
├── openclaw/              # AI assistant (Dockerfile, MCP server, config)
├── templates/             # Jinja2 templates
└── static/                # Images, favicon
```

## API Endpoints

### Public
- `GET /` Landing page
- `GET /leg`, `/evl`, `/zev`, `/vergleich-leg-evl-zev` Info pages
- `GET /impressum`, `/datenschutz` Legal pages
- `GET /confirm/<token>` Email confirmation
- `GET /unsubscribe/<token>` Unsubscribe

### API
- `GET /api/suggest_addresses?q=` Address autocomplete
- `GET /api/get_all_buildings` Anonymized building locations
- `GET /api/get_all_clusters` Cluster polygons
- `POST /api/check_potential` Check address for matches
- `POST /api/register_anonymous` Register (email only)
- `POST /api/register_full` Full registration
- `GET /api/stats/public` Public stats
- `GET /api/referral/leaderboard` Top referrers

### Admin (requires X-Admin-Token header)
- `GET /admin/overview` Dashboard

## License

[To be determined]

**BadenLEG** by Sihl Icon Valley @ sihliconvalley.ch
