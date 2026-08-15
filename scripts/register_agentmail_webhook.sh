#!/bin/sh
set -eu

AGENTMAIL_API_BASE="${AGENTMAIL_API_BASE:-https://api.agentmail.to/v0}"
AGENTMAIL_HUMAN_EMAIL="${AGENTMAIL_HUMAN_EMAIL:-hallo@mail.openleg.ch}"
LEA_AGENT_ID="${LEA_AGENT_ID:-openleg-lea}"
LEA_INBOX_ADDRESS="${LEA_INBOX_ADDRESS:-lea@mail.openleg.ch}"
MODE="${1:-bootstrap}"

require_api_key() {
  if [ -z "${AGENTMAIL_API_KEY:-}" ]; then
    echo "AGENTMAIL_API_KEY is required" >&2
    exit 1
  fi
}

APP_BASE_URL_NORMALIZED="${APP_BASE_URL:-}"
APP_BASE_URL_NORMALIZED="${APP_BASE_URL_NORMALIZED%/}"
WEBHOOK_URL="${AGENTMAIL_WEBHOOK_URL:-${APP_BASE_URL_NORMALIZED}/api/internal/agentmail}"

agent_sign_up() {
  curl -fsS -X POST "${AGENTMAIL_API_BASE}/agent/sign-up" \
    -H "Content-Type: application/json" \
    -d "{
      \"human_email\": \"${AGENTMAIL_HUMAN_EMAIL}\",
      \"username\": \"${LEA_AGENT_ID}\",
      \"source\": \"openclaw\",
      \"referrer\": \"openleg\"
    }"
}

agent_verify() {
  require_api_key
  if [ -z "${AGENTMAIL_OTP_CODE:-}" ]; then
    echo "AGENTMAIL_OTP_CODE is required" >&2
    exit 1
  fi
  curl -fsS -X POST "${AGENTMAIL_API_BASE}/agent/verify" \
    -H "Authorization: Bearer ${AGENTMAIL_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"otp_code\": \"${AGENTMAIL_OTP_CODE}\"}"
}

ensure_inbox() {
  require_api_key
  if curl -fsS -G "${AGENTMAIL_API_BASE}/inboxes" \
       -H "Authorization: Bearer ${AGENTMAIL_API_KEY}" \
       -H "Accept: application/json" \
       --data-urlencode "inbox_id=${LEA_INBOX_ADDRESS}" 2>/dev/null | grep -q "\"inbox_id\":\"${LEA_INBOX_ADDRESS}\""; then
    echo "Inbox ${LEA_INBOX_ADDRESS} already exists, skipping create"
    return 0
  fi
  inbox_user="${LEA_INBOX_ADDRESS%@*}"
  inbox_domain="${LEA_INBOX_ADDRESS#*@}"
  curl -fsS -X POST "${AGENTMAIL_API_BASE}/inboxes" \
    -H "Authorization: Bearer ${AGENTMAIL_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{
      \"username\": \"${inbox_user}\",
      \"domain\": \"${inbox_domain}\",
      \"display_name\": \"OpenLEG LEA\",
      \"client_id\": \"openleg-lea-inbox\",
      \"metadata\": {
        \"app\": \"openleg\",
        \"agent\": \"${LEA_AGENT_ID}\"
      }
    }"
}

register_webhook() {
  require_api_key
  if [ -z "${APP_BASE_URL:-}" ] && [ -z "${AGENTMAIL_WEBHOOK_URL:-}" ]; then
    echo "APP_BASE_URL or AGENTMAIL_WEBHOOK_URL is required" >&2
    exit 1
  fi
  curl -fsS -X POST "${AGENTMAIL_API_BASE}/webhooks" \
  -H "Authorization: Bearer ${AGENTMAIL_API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{
    \"url\": \"${WEBHOOK_URL}\",
    \"event_types\": [
      \"message.received\",
      \"message.received.unauthenticated\"
    ],
    \"inbox_ids\": [\"${LEA_INBOX_ADDRESS}\"],
    \"client_id\": \"openleg-lea-agentmail-webhook\"
  }"
}

case "${MODE}" in
  sign-up)
    agent_sign_up
    ;;
  verify)
    agent_verify
    ;;
  ensure-inbox)
    ensure_inbox
    ;;
  register-webhook)
    register_webhook
    ;;
  bootstrap)
    ensure_inbox
    printf '\n'
    register_webhook
    ;;
  *)
    echo "Usage: $0 [sign-up|verify|ensure-inbox|register-webhook|bootstrap]" >&2
    exit 2
    ;;
esac
