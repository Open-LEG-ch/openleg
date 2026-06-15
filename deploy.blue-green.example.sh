#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  deploy.blue-green.example.sh [--dry-run]

Public-safe blue-green deploy template. Host inventory, secrets, and production
runbooks belong in the private openleg-ops repo.

Required env vars:
  DEPLOY_HOST   (e.g. ubuntu@1.2.3.4)
  REMOTE_DIR    (e.g. /opt/openleg)

Optional env vars:
  SSH_KEY             (path to private key)
  LOCAL_DIR           (defaults to current directory)
  RUN_TESTS           (1 default, set 0 to skip)
  TEST_CMD            (defaults to: pytest tests/ -q)
  HEALTH_URL          (defaults to: https://openleg.ch/health)
  COMPOSE_CMD         (defaults to: docker compose)
  COMPOSE_FILE        (defaults to: docker-compose.blue-green.example.yml)
  IMAGE_REPO          (defaults to: openleg-app)
  POSTGRES_USER       (defaults to compose file default)
  POSTGRES_DB         (defaults to compose file default)
  ACTIVE_SLOT_FILE    (defaults to: .openleg-active-slot)
  DRAIN_SECONDS       (defaults to: 10)
  RSYNC_DELETE        (0 default; set 1 to mirror and remove remote-only files)
  PROTECT_PATHS       (space-separated extra rsync excludes)
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
HEALTH_URL="${HEALTH_URL:-https://openleg.ch/health}"
COMPOSE_CMD="${COMPOSE_CMD:-docker compose}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.blue-green.example.yml}"
IMAGE_REPO="${IMAGE_REPO:-openleg-app}"
POSTGRES_USER="${POSTGRES_USER:-}"
POSTGRES_DB="${POSTGRES_DB:-}"
ACTIVE_SLOT_FILE="${ACTIVE_SLOT_FILE:-.openleg-active-slot}"
DRAIN_SECONDS="${DRAIN_SECONDS:-10}"
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
  echo "  compose_file=$COMPOSE_FILE"
  echo "  image_repo=$IMAGE_REPO"
  echo "  health_url=$HEALTH_URL"
  echo "  rsync_delete=$RSYNC_DELETE"
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

echo "==> Build inactive slot and switch Caddy"
ssh "${SSH_ARGS[@]}" "$DEPLOY_HOST" \
  "REMOTE_DIR='$REMOTE_DIR' COMPOSE_CMD='$COMPOSE_CMD' COMPOSE_FILE='$COMPOSE_FILE' IMAGE_REPO='$IMAGE_REPO' POSTGRES_USER='$POSTGRES_USER' POSTGRES_DB='$POSTGRES_DB' ACTIVE_SLOT_FILE='$ACTIVE_SLOT_FILE' DRAIN_SECONDS='$DRAIN_SECONDS' bash -s" <<'REMOTE'
set -euo pipefail

cd "$REMOTE_DIR"
compose="$COMPOSE_CMD -f $COMPOSE_FILE"
if [[ -n "${POSTGRES_USER:-}" ]]; then
  export POSTGRES_USER
fi
if [[ -n "${POSTGRES_DB:-}" ]]; then
  export POSTGRES_DB
fi

render_caddy() {
  slot="$1"
  upstream="flask-$slot:5000"
  sed "s/{{UPSTREAM}}/$upstream/g" Caddyfile.blue-green.example > Caddyfile.blue-green
}

current="$(cat "$ACTIVE_SLOT_FILE" 2>/dev/null || true)"
if [[ "$current" != "blue" && "$current" != "green" ]]; then
  current="blue"
fi
if [[ "$current" == "blue" ]]; then
  inactive="green"
else
  inactive="blue"
fi

render_caddy "$current"
if ! docker image inspect "$IMAGE_REPO:$current" >/dev/null 2>&1; then
  docker build -t "$IMAGE_REPO:$current" .
fi
OPENLEG_IMAGE_REPO="$IMAGE_REPO" $compose up -d postgres redis "flask-$current" caddy

docker build -t "$IMAGE_REPO:$inactive" .
OPENLEG_IMAGE_REPO="$IMAGE_REPO" $compose up -d --no-deps --force-recreate "flask-$inactive"

for _ in $(seq 1 30); do
  if docker exec "openleg-flask-$inactive" curl -fsS http://localhost:5000/health >/dev/null; then
    break
  fi
  sleep 2
done
docker exec "openleg-flask-$inactive" curl -fsS http://localhost:5000/health >/dev/null

render_caddy "$inactive"
docker exec openleg-caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
echo "$inactive" > "$ACTIVE_SLOT_FILE"
REMOTE

echo "==> Check public health"
if ! curl -fsS "$HEALTH_URL" >/dev/null; then
  echo "Public health failed; rolling Caddy back to previous slot." >&2
  ssh "${SSH_ARGS[@]}" "$DEPLOY_HOST" \
    "cd '$REMOTE_DIR' && previous=\$(cat '$ACTIVE_SLOT_FILE') && if [[ \"\$previous\" == blue ]]; then rollback=green; else rollback=blue; fi && sed \"s/{{UPSTREAM}}/flask-\$rollback:5000/g\" Caddyfile.blue-green.example > Caddyfile.blue-green && docker exec openleg-caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile && echo \"\$rollback\" > '$ACTIVE_SLOT_FILE'"
  exit 1
fi

echo "==> Drain previous slot"
ssh "${SSH_ARGS[@]}" "$DEPLOY_HOST" \
  "cd '$REMOTE_DIR' && active=\$(cat '$ACTIVE_SLOT_FILE') && if [[ \"\$active\" == blue ]]; then previous=green; else previous=blue; fi && sleep '$DRAIN_SECONDS' && OPENLEG_IMAGE_REPO='$IMAGE_REPO' POSTGRES_USER='$POSTGRES_USER' POSTGRES_DB='$POSTGRES_DB' $COMPOSE_CMD -f '$COMPOSE_FILE' stop \"flask-\$previous\" >/dev/null || true"

echo "==> Blue-green deploy finished"
