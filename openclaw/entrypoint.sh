#!/bin/sh
# Write Docker env vars to OpenClaw's .env so ${VAR} interpolation works in openclaw.json
cat > /home/node/.openclaw/.env <<EOF
GROQ_API_KEY=${GROQ_API_KEY}
OPENCLAW_GATEWAY_TOKEN=${OPENCLAW_GATEWAY_TOKEN}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
DATABASE_URL=${DATABASE_URL}
BRAVE_API_KEY=${BRAVE_API_KEY}
OPENCLAW_READONLY=${OPENCLAW_READONLY:-false}
EOF

exec openclaw gateway --allow-unconfigured --bind lan
