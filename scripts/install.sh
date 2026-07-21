#!/usr/bin/env bash
set -euo pipefail

# Quick installer for a checked-out OpenLEG repository.
ENV_FILE=".env"
OPENLEG_HTTP_PORT="${OPENLEG_HTTP_PORT:-8080}"
export OPENLEG_HTTP_PORT

if ! command -v docker >/dev/null 2>&1; then
    echo "Error: Docker is required but was not found." >&2
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    echo "Error: Docker Compose is required but is not working." >&2
    exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
    echo "Error: curl is required for the health check but was not found." >&2
    exit 1
fi

if [ -f "$ENV_FILE" ]; then
    echo "$ENV_FILE already exists; keeping the existing secrets."
else
    echo "Creating $ENV_FILE with fresh secrets..."
    POSTGRES_PASSWORD="$(openssl rand -hex 24)"
    SECRET_KEY="$(openssl rand -hex 32)"
    ADMIN_TOKEN="$(openssl rand -hex 32)"
    INTERNAL_TOKEN="$(openssl rand -hex 32)"
    CRON_SECRET="$(openssl rand -hex 32)"

    umask 077
    printf '%s\n' \
        "POSTGRES_USER=openleg" \
        "POSTGRES_PASSWORD=$POSTGRES_PASSWORD" \
        "POSTGRES_DB=openleg" \
        "SECRET_KEY=$SECRET_KEY" \
        "ADMIN_TOKEN=$ADMIN_TOKEN" \
        "INTERNAL_TOKEN=$INTERNAL_TOKEN" \
        "CRON_SECRET=$CRON_SECRET" \
        "APP_BASE_URL=http://localhost:$OPENLEG_HTTP_PORT" \
        "ALLOWED_HOSTS=localhost,127.0.0.1" > "$ENV_FILE"
fi

echo "Starting OpenLEG..."
docker compose -f docker-compose.yml -f docker-compose.quickstart.yml up -d --build flask postgres redis

echo "Waiting for the app to become healthy..."
for attempt in $(seq 1 60); do
    if curl -fsS "http://localhost:$OPENLEG_HTTP_PORT/livez" >/dev/null; then
        echo "OpenLEG is ready at http://localhost:$OPENLEG_HTTP_PORT"
        echo "Next steps:"
        echo "  1. Open the URL in your browser."
        echo "  2. Keep $ENV_FILE private and back it up securely."
        echo "  3. Run 'docker compose logs -f flask' to view logs."
        exit 0
    fi
    sleep 2
done

echo "Error: OpenLEG did not become healthy in time." >&2
exit 1
