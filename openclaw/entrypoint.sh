#!/bin/sh
set -eu

OPENCLAW_GATEWAY_BIND="${OPENCLAW_GATEWAY_BIND:-loopback}"
OPENCLAW_GATEWAY_PORT="${OPENCLAW_GATEWAY_PORT:-18789}"
OPENCLAW_READONLY="${OPENCLAW_READONLY:-true}"

mkdir -p /home/node/.openclaw /home/node/.openclaw/workspace /home/node/.openclaw/cron

# Copy config only when the destination is not already provided (e.g. via mount).
# Use "|| true" so a read-only mount never crash-loops the container.
if [ ! -f /home/node/.openclaw/openclaw.json ]; then
  if [ -f /opt/openclaw-config/openclaw.json ]; then
    cp /opt/openclaw-config/openclaw.json /home/node/.openclaw/openclaw.json 2>/dev/null || true
  else
    cp /opt/openclaw-config/openclaw.example.json /home/node/.openclaw/openclaw.json 2>/dev/null || true
  fi
fi

if [ -d /opt/openclaw-config/cron ]; then
  cp -R /opt/openclaw-config/cron/. /home/node/.openclaw/cron/ 2>/dev/null || true
fi

if [ -d /opt/openclaw-config/workspace ]; then
  cp -R /opt/openclaw-config/workspace/. /home/node/.openclaw/workspace/ 2>/dev/null || true
fi

# Write Docker env vars to OpenClaw's .env so ${VAR} interpolation works in openclaw.json.
# Optional vars default to empty so a missing one never aborts the container (set -eu).
cat > /home/node/.openclaw/.env <<EOF
MODEL_BASE_URL=${MODEL_BASE_URL:-}
MODEL_API_KEY=${MODEL_API_KEY:-}
MODEL_ID=${MODEL_ID:-}
GROQ_API_KEY=${GROQ_API_KEY:-}
OPENCLAW_GATEWAY_TOKEN=${OPENCLAW_GATEWAY_TOKEN:-}
OPENCLAW_GATEWAY_PASSWORD=${OPENCLAW_GATEWAY_PASSWORD:-}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-}
DATABASE_URL=${DATABASE_URL:-}
BRAVE_API_KEY=${BRAVE_API_KEY:-}
OPENCLAW_GATEWAY_BIND=${OPENCLAW_GATEWAY_BIND:-loopback}
OPENCLAW_GATEWAY_PORT=${OPENCLAW_GATEWAY_PORT:-18789}
OPENCLAW_READONLY=${OPENCLAW_READONLY:-true}
INTERNAL_TOKEN=${INTERNAL_TOKEN:-}
FLASK_URL=${FLASK_URL:-http://flask:5000}
AGENTMAIL_API_BASE=${AGENTMAIL_API_BASE:-https://api.agentmail.to/v0}
AGENTMAIL_API_KEY=${AGENTMAIL_API_KEY:-}
AGENTMAIL_WEBHOOK_SECRET=${AGENTMAIL_WEBHOOK_SECRET:-}
AGENTMAIL_WEBHOOK_URL=${AGENTMAIL_WEBHOOK_URL:-}
AGENTMAIL_HUMAN_EMAIL=${AGENTMAIL_HUMAN_EMAIL:-}
LEA_INBOX_ADDRESS=${LEA_INBOX_ADDRESS:-}
LEA_AGENT_ID=${LEA_AGENT_ID:-openleg-lea}
EOF

exec openclaw gateway \
  --allow-unconfigured \
  --port "${OPENCLAW_GATEWAY_PORT}" \
  --bind "${OPENCLAW_GATEWAY_BIND}" \
  --auth password \
  --password "${OPENCLAW_GATEWAY_PASSWORD}" \
  --token "${OPENCLAW_GATEWAY_TOKEN}"
