#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  deploy.example.sh [--dry-run]

Required env vars:
  DEPLOY_HOST   (e.g. ubuntu@1.2.3.4)
  REMOTE_DIR    (e.g. /opt/openleg)

Optional env vars:
  SSH_KEY                (path to private key)
  LOCAL_DIR              (defaults to current directory)
  RUN_TESTS              (1 default, set 0 to skip)
  TEST_CMD               (defaults to: pytest tests/ -q)
  HEALTH_URL             (optional post-deploy check URL)
  COMPOSE_CMD            (defaults to: docker compose)
  BUILD_PRIMARY_SERVICE  (defaults to: flask)
USAGE
}

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
elif [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
elif [[ -n "${1:-}" ]]; then
  echo "Unknown argument: $1" >&2
  usage
  exit 2
fi

DEPLOY_HOST="${DEPLOY_HOST:-}"
REMOTE_DIR="${REMOTE_DIR:-}"
SSH_KEY="${SSH_KEY:-}"
LOCAL_DIR="${LOCAL_DIR:-$PWD}"
RUN_TESTS="${RUN_TESTS:-1}"
TEST_CMD="${TEST_CMD:-pytest tests/ -q}"
HEALTH_URL="${HEALTH_URL:-}"
COMPOSE_CMD="${COMPOSE_CMD:-docker compose}"
BUILD_PRIMARY_SERVICE="${BUILD_PRIMARY_SERVICE:-flask}"

if [[ -z "$DEPLOY_HOST" || -z "$REMOTE_DIR" ]]; then
  echo "DEPLOY_HOST and REMOTE_DIR are required." >&2
  usage
  exit 1
fi

SSH_ARGS=()
if [[ -n "$SSH_KEY" ]]; then
  SSH_ARGS=(-i "$SSH_KEY")
fi

if [[ "$DRY_RUN" == "1" ]]; then
  echo "Dry-run:"
  echo "  host=$DEPLOY_HOST"
  echo "  remote_dir=$REMOTE_DIR"
  echo "  local_dir=$LOCAL_DIR"
  echo "  compose_cmd=$COMPOSE_CMD"
  echo "  build_primary_service=$BUILD_PRIMARY_SERVICE"
  exit 0
fi

if [[ "$RUN_TESTS" == "1" ]]; then
  echo "==> Running tests"
  bash -lc "$TEST_CMD"
fi

echo "==> Ensure remote dir"
ssh "${SSH_ARGS[@]}" "$DEPLOY_HOST" "mkdir -p '$REMOTE_DIR'"

echo "==> Sync project"
rsync -az --delete \
  --exclude='.git' \
  --exclude='.env' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache' \
  --exclude='node_modules' \
  --exclude='.venv' \
  -e "ssh ${SSH_KEY:+-i $SSH_KEY}" \
  "$LOCAL_DIR/" "$DEPLOY_HOST:$REMOTE_DIR/"

echo "==> Build and start $BUILD_PRIMARY_SERVICE"
ssh "${SSH_ARGS[@]}" "$DEPLOY_HOST" "cd '$REMOTE_DIR' && $COMPOSE_CMD up -d --build '$BUILD_PRIMARY_SERVICE'"

if [[ -n "$HEALTH_URL" ]]; then
  echo "==> Check health URL"
  sleep 5
  curl -fsS "$HEALTH_URL" >/dev/null
fi

echo "==> Deploy finished"
