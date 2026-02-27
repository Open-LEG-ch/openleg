#!/bin/sh
# Write Docker env vars to OpenClaw's .env so ${VAR} interpolation works in openclaw.json
cat > /home/node/.openclaw/.env <<EOF
GROQ_API_KEY=${GROQ_API_KEY}
OPENCLAW_GATEWAY_TOKEN=${OPENCLAW_GATEWAY_TOKEN}
OPENCLAW_GATEWAY_PASSWORD=${OPENCLAW_GATEWAY_PASSWORD}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
DATABASE_URL=${DATABASE_URL}
BRAVE_API_KEY=${BRAVE_API_KEY}
OPENCLAW_READONLY=${OPENCLAW_READONLY:-false}
EOF

# Start gateway in background
openclaw gateway --allow-unconfigured --bind lan --auth password --password "${OPENCLAW_GATEWAY_PASSWORD}" --token "${OPENCLAW_GATEWAY_TOKEN}" &
GW_PID=$!

# Wait for gateway to accept connections (up to 60s)
echo "[entrypoint] waiting for gateway..."
i=0
while [ $i -lt 60 ]; do
  if curl -sf http://localhost:18789/ > /dev/null 2>&1; then
    echo "[entrypoint] gateway ready after ${i}s"
    break
  fi
  i=$((i + 1))
  sleep 1
done

if [ $i -eq 60 ]; then
  echo "[entrypoint] WARNING: gateway not ready after 60s, registering cron jobs anyway"
fi

# Helper: add cron job only if name doesn't already exist
add_if_missing() {
  name="$1"; shift
  existing=$(openclaw cron list --json 2>/dev/null | grep -c "\"name\":\"${name}\"" || true)
  if [ "$existing" = "0" ]; then
    openclaw cron add --name "$name" "$@" 2>&1 && echo "[entrypoint] added: $name" || echo "[entrypoint] FAILED: $name"
  else
    echo "[entrypoint] exists: $name"
  fi
}

add_if_missing "daily-health-check" \
  --cron "0 7 * * *" --tz "Europe/Zurich" \
  --session isolated --timeout-seconds 120 --no-deliver \
  --message "Run daily community health check: exec node /opt/mcp-badenleg-server/cli.mjs get_stats, then node /opt/mcp-badenleg-server/cli.mjs list_communities, check stuck formations >7d via node /opt/mcp-badenleg-server/cli.mjs get_stuck_formations --days 7. Report active/blocked formations and today registrations."

add_if_missing "weekly-municipality-seeding" \
  --cron "0 6 * * 1" --tz "Europe/Zurich" \
  --session isolated --timeout-seconds 300 --no-deliver \
  --message "Seed 50 new municipalities: exec node /opt/mcp-badenleg-server/cli.mjs get_unseeded_municipalities --limit 50, then for each run node /opt/mcp-badenleg-server/cli.mjs upsert_tenant with appropriate config. Report seeded count and failures."

add_if_missing "weekly-data-refresh" \
  --cron "0 3 * * 3" --tz "Europe/Zurich" \
  --session isolated --timeout-seconds 300 --no-deliver \
  --message "Refresh public data: exec node /opt/mcp-badenleg-server/cli.mjs fetch_elcom_tariffs, fetch_energie_reporter, fetch_sonnendach_data for seeded municipalities. Then refresh_municipality_data. Report updated count and data gaps."

add_if_missing "monthly-vnb-transparency" \
  --cron "0 5 1 * *" --tz "Europe/Zurich" \
  --session isolated --timeout-seconds 300 --no-deliver \
  --message "Monthly VNB transparency run: exec node /opt/mcp-badenleg-server/cli.mjs scan_vnb_leg_offerings for major VNBs, monitor_leghub_partners for changes, compute transparency scores. Report VNBs scored and changes."

echo "[entrypoint] cron setup done"

# Wait on gateway process
wait $GW_PID
