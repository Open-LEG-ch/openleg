#!/bin/sh
# Copy config into volume (preserves runtime data like device pairings across rebuilds)
cp /opt/openclaw-config/openclaw.json /home/node/.openclaw/openclaw.json 2>/dev/null || true

# Copy workspace files (SOUL, USER, TOOLS, HEARTBEAT) only if not already customized
mkdir -p /home/node/.openclaw/workspace
for f in SOUL.md USER.md TOOLS.md HEARTBEAT.md FEEDBACK.md; do
  if [ ! -f "/home/node/.openclaw/workspace/$f" ] || [ "/opt/openclaw-workspace/$f" -nt "/home/node/.openclaw/workspace/$f" ]; then
    cp "/opt/openclaw-workspace/$f" "/home/node/.openclaw/workspace/$f" 2>/dev/null || true
  fi
done

# Write Docker env vars to OpenClaw's .env so ${VAR} interpolation works in openclaw.json
cat > /home/node/.openclaw/.env <<EOF
GROQ_API_KEY=${GROQ_API_KEY}
XAI_API_KEY=${XAI_API_KEY}
OPENCLAW_GATEWAY_TOKEN=${OPENCLAW_GATEWAY_TOKEN}
OPENCLAW_GATEWAY_PASSWORD=${OPENCLAW_GATEWAY_PASSWORD}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
DATABASE_URL=${DATABASE_URL}
BRAVE_API_KEY=${BRAVE_API_KEY}
OPENCLAW_READONLY=${OPENCLAW_READONLY:-false}
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID}
INTERNAL_TOKEN=${INTERNAL_TOKEN}
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
  --session isolated --timeout-seconds 120 --announce \
  --message "Run daily community health check: exec node /opt/mcp-openleg-server/cli.mjs get_stats, then node /opt/mcp-openleg-server/cli.mjs list_communities, check stuck formations >7d via node /opt/mcp-openleg-server/cli.mjs get_stuck_formations --days 7. Report active/blocked formations and today registrations."

add_if_missing "weekly-municipality-seeding" \
  --cron "0 6 * * 1" --tz "Europe/Zurich" \
  --session isolated --timeout-seconds 300 --announce \
  --message "Seed 50 new municipalities: exec node /opt/mcp-openleg-server/cli.mjs get_unseeded_municipalities --limit 50, then for each run node /opt/mcp-openleg-server/cli.mjs upsert_tenant with appropriate config. Report seeded count and failures."

add_if_missing "weekly-data-refresh" \
  --cron "0 3 * * 3" --tz "Europe/Zurich" \
  --session isolated --timeout-seconds 300 --announce \
  --message "Refresh public data: exec node /opt/mcp-openleg-server/cli.mjs fetch_elcom_tariffs, fetch_energie_reporter, fetch_sonnendach_data for seeded municipalities. Then refresh_municipality_data. Report updated count and data gaps."

add_if_missing "monthly-vnb-transparency" \
  --cron "0 5 1 * *" --tz "Europe/Zurich" \
  --session isolated --timeout-seconds 300 --announce \
  --message "Monthly VNB transparency run: exec node /opt/mcp-openleg-server/cli.mjs scan_vnb_leg_offerings for major VNBs, monitor_leghub_partners for changes, compute transparency scores. Report VNBs scored and changes."

add_if_missing "strategy-standup" \
  --cron "0 8 * * *" --tz "Europe/Zurich" \
  --session isolated --timeout-seconds 180 \
  --announce \
  --message "Daily strategy standup: 1) exec node /opt/mcp-openleg-server/cli.mjs get_strategy_status, review pending/blocked items. 2) exec node /opt/mcp-openleg-server/cli.mjs get_stats for pipeline metrics. 3) Check stuck formations >7d. 4) Send daily_report via send_telegram with summary of strategy progress, pipeline health, and any needs_ceo items. Flag blockers immediately."

add_if_missing "weekly-research-scan" \
  --cron "0 4 * * 0" --tz "Europe/Zurich" \
  --session isolated --timeout-seconds 300 --announce \
  --message "Weekly research scan. Follow the Plan-Research-Synthesize protocol: 1) PLAN: search for BFE grant updates, Swiss energy regulation changes (StromVG, LEG Verordnung), and competitor activity (LEGHub, Ormera, Optimatik, Exnaton). 2) RESEARCH: search_web for each topic, collect top findings with source URLs. 3) SYNTHESIZE: summarize as 2-3 bullet points per category, marking items as NEW or CHANGED. Send full summary via send_telegram (category: daily_report)."

add_if_missing "vnb-outreach-cycle" \
  --cron "0 9 * * 2,4" --tz "Europe/Zurich" \
  --session isolated --timeout-seconds 300 \
  --announce \
  --message "VNB outreach cycle. Follow the Plan-Research-Synthesize protocol from SOUL.md: 1) PLAN: get_outreach_candidates --limit 10, identify top 3. 2) RESEARCH: For each, research_vnb + draft_outreach to gather data. 3) SYNTHESIZE: Produce structured assessment (verdict, key metrics, risk factors) per TOOLS.md format, then draft outreach for HOT/WARM candidates. 4) request_approval for CEO sign-off. 5) Check get_decisions for recently approved/denied items."

add_if_missing "pipeline-review" \
  --cron "0 10 * * 5" --tz "Europe/Zurich" \
  --session isolated --timeout-seconds 180 \
  --announce \
  --message "Weekly pipeline review: 1) exec node /opt/mcp-openleg-server/cli.mjs get_stats. 2) exec node /opt/mcp-openleg-server/cli.mjs list_communities --status formation_started, check for stale leads >14d. 3) exec node /opt/mcp-openleg-server/cli.mjs get_strategy_status for weekly summary. 4) Send weekly Telegram report via send_telegram (category: daily_report) with funnel metrics, stale leads, stuck formations, strategy progress."

add_if_missing "municipality-pipeline" \
  --cron "0 11 * * 3" --tz "Europe/Zurich" \
  --session isolated --timeout-seconds 300 \
  --announce \
  --message "Municipality pipeline: exec node /opt/mcp-openleg-server/cli.mjs run_municipality_pipeline --max_municipalities 3. Pipeline discovers high-potential municipalities, creates tenants, drafts outreach, requests CEO approval. Report results via send_telegram (category: daily_report)."

add_if_missing "auto-followup-check" \
  --cron "0 10 * * 1,3,5" --tz "Europe/Zurich" \
  --session isolated --timeout-seconds 120 \
  --announce \
  --message "Stale outreach follow-up: 1) exec node /opt/mcp-openleg-server/cli.mjs get_stale_outreach --days_threshold 7. 2) For each stale item, draft a follow-up email. 3) Use request_approval for CEO sign-off. 4) Send summary via send_telegram (category: daily_report)."

echo "[entrypoint] cron setup done"

# Wait on gateway process
wait $GW_PID
