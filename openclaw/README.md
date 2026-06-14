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

## Optional env

- `OPENCLAW_GATEWAY_BIND`
- `OPENCLAW_GATEWAY_PORT`
- `OPENCLAW_READONLY`
- `BRAVE_API_KEY`

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
