#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-https://openleg.ch}"
DEMO_HOST="${DEMO_HOST:-newbaden.openleg.ch}"

echo "[1/3] check demo status at ${BASE_URL}/gemeinde/demo/status"
status_json="$(curl -sS "${BASE_URL}/gemeinde/demo/status")"
echo "${status_json}" | grep -q '"enabled":true\|"enabled": true'

echo "[2/3] trigger demo provisioning"
payload='{
  "municipality_name":"Newbaden",
  "contact_name":"Demo Operator",
  "contact_email":"demo@newbaden.ch",
  "kanton":"Aargau",
  "kanton_code":"AG",
  "population":22000,
  "dso_name":"Regionalwerke Baden"
}'
provision_json="$(curl -sS -X POST "${BASE_URL}/gemeinde/demo/provision" \
  -H "Content-Type: application/json" \
  --data "${payload}")"
echo "${provision_json}" | grep -q '"success":true\|"success": true'

echo "[3/3] verify host render https://${DEMO_HOST}/"
curl -fsS "https://${DEMO_HOST}/" >/dev/null

echo "smoke ok"
