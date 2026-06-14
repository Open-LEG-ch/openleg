#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  deploy.example.sh [--dry-run]

Public-safe deploy template. Production host inventory and runbooks live in the
private openleg-ops repo. Connect with the user your host allows (many setups
disable root login, e.g. DEPLOY_HOST=ubuntu@1.2.3.4).

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
  RSYNC_DELETE           (0 default; set 1 to mirror and remove remote-only files)
  PROTECT_PATHS          (space-separated extra rsync excludes, always preserved)

Safety:
  rsync runs WITHOUT --delete by default, so remote-only files (database
  backups, generated assets, private dirs) are never removed. Enable
  RSYNC_DELETE=1 only when you have verified the remote has no unmanaged data.
  Even then, PROTECT_PATHS entries are always excluded. Run --dry-run first to
  preview every change, including any deletions.
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
RSYNC_DELETE="${RSYNC_DELETE:-0}"
PROTECT_PATHS="${PROTECT_PATHS:-}"

if [[ -z "$DEPLOY_HOST" || -z "$REMOTE_DIR" ]]; then
  echo "DEPLOY_HOST and REMOTE_DIR are required." >&2
  usage
  exit 1
fi

SSH_ARGS=()
if [[ -n "$SSH_KEY" ]]; then
  SSH_ARGS=(-i "$SSH_KEY")
fi

# Build rsync options. --delete is opt-in. Runtime/data paths that the repo does
# not own are always excluded so a mirror can never wipe production state.
RSYNC_OPTS=(-az)
if [[ "$RSYNC_DELETE" == "1" ]]; then
  RSYNC_OPTS+=(--delete)
fi
RSYNC_OPTS+=(
  --exclude='.git'
  --exclude='.env'
  --exclude='__pycache__'
  --exclude='*.pyc'
  --exclude='.pytest_cache'
  --exclude='node_modules'
  --exclude='.venv'
  --exclude='backups/'
  --exclude='*.sql'
  --exclude='*.sql.gz'
  --exclude='output/'
  --exclude='tmp/'
  --exclude='overnight/'
  --exclude='prd/'
  --exclude='grants/'
  --exclude='outreach/'
)
for p in $PROTECT_PATHS; do
  RSYNC_OPTS+=(--exclude="$p")
done

if [[ "$DRY_RUN" == "1" ]]; then
  echo "Dry-run:"
  echo "  host=$DEPLOY_HOST"
  echo "  remote_dir=$REMOTE_DIR"
  echo "  local_dir=$LOCAL_DIR"
  echo "  compose_cmd=$COMPOSE_CMD"
  echo "  build_primary_service=$BUILD_PRIMARY_SERVICE"
  echo "  rsync_delete=$RSYNC_DELETE"
  echo "==> rsync preview (no changes written)"
  rsync "${RSYNC_OPTS[@]}" --dry-run --itemize-changes \
    -e "ssh ${SSH_KEY:+-i $SSH_KEY}" \
    "$LOCAL_DIR/" "$DEPLOY_HOST:$REMOTE_DIR/"
  exit 0
fi

if [[ "$RUN_TESTS" == "1" ]]; then
  echo "==> Running tests"
  bash -lc "$TEST_CMD"
fi

echo "==> Ensure remote dir"
ssh "${SSH_ARGS[@]}" "$DEPLOY_HOST" "mkdir -p '$REMOTE_DIR'"

echo "==> Sync project (delete=$RSYNC_DELETE)"
rsync "${RSYNC_OPTS[@]}" \
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
