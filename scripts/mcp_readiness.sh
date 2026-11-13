#!/usr/bin/env bash
# Pinned production readiness gate; requires the test PostgreSQL environment.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python -m scripts.mcp_readiness "$@"
