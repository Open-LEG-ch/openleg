#!/usr/bin/env bash
# OpenLEG canonical test harness.
#
# One command we run on every build, deploy, and debug:
#
#   scripts/test.sh [fast|full|gate]
#
#   fast  quick inner loop for TDD: unit + contract layers, no services needed
#   full  the whole suite (integration/smoke layers run when services exist)
#   gate  what a pull request must pass: full pytest + ruff lint + format check
#
# Default mode is gate, so a bare `scripts/test.sh` is the pre-PR check.
set -euo pipefail

cd "$(dirname "$0")/.."

MODE="${1:-gate}"

case "$MODE" in
  fast)
    pytest tests/ -q -m "not integration and not smoke"
    ;;
  full)
    pytest tests/ -q
    ;;
  gate)
    pytest tests/ -q
    ruff check .
    ruff format --check .
    ;;
  *)
    echo "usage: scripts/test.sh [fast|full|gate]" >&2
    exit 2
    ;;
esac
