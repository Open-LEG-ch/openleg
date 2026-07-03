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

# Prove the full regression gate runs before network access is restricted:
# a green gate here guarantees the offline environment has everything the
# tests and linters need, including native dependencies.
bash scripts/tdd_cycle.sh gate

echo "codex setup complete"
