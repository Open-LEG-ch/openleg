#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Environment setup script for OpenAI Codex cloud environments.
# Configure this as the environment "setup script" so dependencies are
# installed while network access is still available. After setup, Codex
# runs with restricted network, so everything the gates need must be
# installed here.
#
# Gates after setup:
#   scripts/tdd_cycle.sh gate   (pytest + ruff check + ruff format --check)

set -euo pipefail

cd "$(dirname "$0")/.."

python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt

# Smoke check: the full regression gate must be runnable offline.
python -c "import flask, pytest" >/dev/null
ruff --version >/dev/null

echo "codex setup complete"
