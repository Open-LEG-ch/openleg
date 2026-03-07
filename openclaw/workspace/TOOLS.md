# TOOLS.md - LEA MCP Tools Reference

## Model Stack

| Role | Model | Provider | Use |
|------|-------|----------|-----|
| Primary | GPT-OSS 120B | Groq | Data tasks, health checks, Telegram, tool calling |
| Fallback | Qwen3 32B | Groq | Multilingual safety net, function calling |
| Escalation | Kimi K2 | Groq | Complex research, long tool chains |
| Outreach | Grok 3 Fast | xAI | German email drafting |

## Email Inboxes

- **LEA inbox:** lea@mail.openleg.ch (outreach, follow-ups)
- **Transactional:** hallo@mail.openleg.ch (system emails, drip campaigns)

## Outreach Workflow

1. `get_outreach_candidates` → list municipalities needing outreach
2. `draft_outreach --bfs_number <N>` → enriched data brief (NOT a finished email)
3. **You write the email** using the brief data + style_guide + feedback
4. `send_outreach_email` → queues for CEO approval (RED tier)
5. CEO approves via Telegram: `approve <request_id>`

### draft_outreach return shape

```json
{
  "brief": {
    "municipality_name": "Baden",
    "bfs_number": 4021,
    "kanton": "AG",
    "population": 19200,
    "tariff_total_rp_kwh": 27.3,
    "grid_rp_kwh": 9.8,
    "operator_name": "Regionalwerke AG Baden",
    "cantonal_avg_tariff_rp_kwh": 25.1,
    "cantonal_rank": "12/213",
    "above_cantonal_avg": true,
    "solar_potential_pct": 42.5,
    "ev_share_pct": 8.3,
    "renewable_heating_pct": 22.1,
    "energy_transition_score": 67,
    "leg_value_gap_chf": 185
  },
  "urls": {
    "profile": "https://openleg.ch/gemeinde/profil/4021",
    "case_study": "https://openleg.ch/pilotgemeinde/baden",
    "onboarding": "https://openleg.ch/gemeinde/onboarding"
  },
  "style_guide": "...",
  "feedback": "..."
}
```

## Follow-up Workflow

1. `get_stale_outreach --days_threshold 7` → stale items
2. Write follow-up email (shorter, reference original)
3. `send_outreach_email` → queue for approval

## Seeding Workflow

1. `get_unseeded_municipalities --limit 50`
2. `upsert_tenant` for each (YELLOW tier, 10/day)

## Data Refresh

1. `fetch_elcom_tariffs` → ElCom SPARQL
2. `fetch_energie_reporter` → opendata.swiss
3. `fetch_sonnendach_data` → BFE solar data

## Budgets (daily)

| Tool | Tier | Limit |
|------|------|-------|
| send_outreach_email | RED | 20/day |
| trigger_email | RED | 50/day |
| upsert_tenant | YELLOW | 10/day |
| create_community | YELLOW | 5/day |
| add_community_member | YELLOW | 50/day |
| run_municipality_pipeline | YELLOW | 3/day |
| send_telegram | GREEN | 30/hour |

## Structured Analysis Output

When analyzing a municipality, format your assessment as:

```json
{
  "bfs_number": 4021,
  "municipality": "Baden",
  "analysis_date": "2026-03-10",
  "verdict": "HOT",
  "confidence": 0.85,
  "key_metrics": {
    "tariff_rp_kwh": 27.3,
    "cantonal_rank": "12/213",
    "value_gap_chf": 185,
    "solar_potential_pct": 42.5
  },
  "risk_factors": ["VNB not on LEGHub", "Population under 20K"],
  "recommended_action": "Draft outreach email this cycle",
  "reasoning": "High tariff + above cantonal avg + good solar = strong LEG case"
}
```

Use this format when reporting municipality analysis via send_telegram (category: daily_report).

## Flask Endpoints

- POST /api/internal/send-email (AGENT_EMAIL_ENABLED=true)
- POST /api/internal/request-approval (RED tier approval flow)
- POST /api/internal/check-budget (budget enforcement)
- POST /api/internal/notify-yellow (YELLOW tier notifications)

## Key Data

- 2131 Swiss municipalities
- 631 DSOs
- LEG law: Art. 17d/17e StromVG, 40% network discount same level, 20% cross-level
- Median tariff: 27.7 Rp/kWh, value gap: 2-6 Rp/kWh
- Competitors: LEGHub (4 clients), smart-me, OWT (Romandie)
