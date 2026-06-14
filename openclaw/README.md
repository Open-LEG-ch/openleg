# OpenClaw

Public-safe OpenClaw bundle for OpenLEG.

## Scope

- `openclaw/config/openclaw.example.json`: safe template only
- `openclaw/config/openclaw.json`: local/private, never commit
- `openclaw/config/cron/`: local/private jobs, never commit
- `openclaw/mcp-openleg-server/server.mjs`: MCP server exposing OpenLEG tools

## Defaults

- gateway bind: `loopback`
- gateway port: `18789`
- auth: password + token
- cron: disabled in the public example
- readonly: `true` unless you explicitly switch it off

## Required env

- `MODEL_BASE_URL`
- `MODEL_API_KEY`
- `MODEL_ID`
- `OPENCLAW_GATEWAY_TOKEN`
- `OPENCLAW_GATEWAY_PASSWORD`
- `DATABASE_URL`
- `INTERNAL_TOKEN`

## Optional env

- `OPENCLAW_GATEWAY_BIND`
- `OPENCLAW_GATEWAY_PORT`
- `OPENCLAW_READONLY`
- `BRAVE_API_KEY`
- `FLASK_URL`
- `AGENTMAIL_API_BASE`
- `AGENTMAIL_API_KEY`
- `AGENTMAIL_WEBHOOK_SECRET`
- `AGENTMAIL_WEBHOOK_URL`
- `AGENTMAIL_HUMAN_EMAIL`
- `LEA_INBOX_ADDRESS`
- `LEA_AGENT_ID`

## Local run

```bash
docker build -t openleg-openclaw ./openclaw
docker run --rm \
  --env-file .env \
  -e DATABASE_URL=postgresql://openleg:password@host.docker.internal:5432/openleg \
  -p 127.0.0.1:18789:18789 \
  openleg-openclaw
```

## Notes

- Keep `OPENCLAW_GATEWAY_BIND=loopback` unless you have a deliberate remote-access design.
- If you reverse-proxy the Control UI, configure `gateway.trustedProxies` in your private `openclaw.json`.
- Switch `OPENCLAW_READONLY=false` only for workflows that must write to the OpenLEG database.
- LEA mail ingestion belongs in your private OpenClaw config and should post structured events to `/api/internal/agentmail`.
- Use `/api/internal/ops-snapshot` for GitHub monitor, VNB monitor, stuck formation, and health snapshots.

## Private AgentMail activation

Keep the script and resulting keys in private ops only. For the OpenLEG LEA inbox:

```bash
AGENTMAIL_HUMAN_EMAIL=hallo@openleg.ch \
LEA_AGENT_ID=openleg-lea \
openclaw/config/cron/register_agentmail_webhook.sh sign-up

AGENTMAIL_API_KEY=am_... \
AGENTMAIL_OTP_CODE=123456 \
openclaw/config/cron/register_agentmail_webhook.sh verify

APP_BASE_URL=https://openleg.ch \
AGENTMAIL_API_KEY=am_... \
LEA_INBOX_ADDRESS=hallo@openleg.ch \
openclaw/config/cron/register_agentmail_webhook.sh bootstrap
```

Store the returned webhook `secret` as `AGENTMAIL_WEBHOOK_SECRET`.
