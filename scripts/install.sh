#!/usr/bin/env bash
set -euo pipefail

REPOSITORY="${OPENLEG_REPOSITORY:-https://github.com/Open-LEG-ch/openleg.git}"
INSTALL_DIR="${OPENLEG_INSTALL_DIR:-$PWD/openleg}"

if [[ -x "$PWD/scripts/openleg" && -f "$PWD/docker-compose.yml" ]]; then
    INSTALL_DIR="$PWD"
elif [[ -d "$INSTALL_DIR/.git" ]]; then
    :
elif [[ -e "$INSTALL_DIR" ]]; then
    echo "Error: $INSTALL_DIR exists but is not an OpenLEG checkout." >&2
    exit 1
else
    if ! command -v git >/dev/null 2>&1; then
        echo "Error: git is required but was not found." >&2
        exit 1
    fi
    git clone --depth 1 "$REPOSITORY" "$INSTALL_DIR"
fi

exec "$INSTALL_DIR/scripts/openleg" install
