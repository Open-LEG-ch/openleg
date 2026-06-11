#!/usr/bin/env bash
# Deterministic TDD cycle runner for pytest-first slices.
# Usage:
#   scripts/tdd_cycle.sh red <pytest-node>
#   scripts/tdd_cycle.sh green <pytest-node>
#   scripts/tdd_cycle.sh refactor <pytest-node>
#   scripts/tdd_cycle.sh gate

set -euo pipefail

print_help() {
  cat <<'EOF'
Usage: scripts/tdd_cycle.sh <command> [pytest-node]

Commands:
  red       Run one targeted pytest node (expected to fail during red phase)
  green     Run one targeted pytest node (expected to pass after implementation)
  refactor  Re-run one targeted pytest node during cleanup
  gate      Run full regression gates (pytest + ruff checks)
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  print_help
  exit 0
fi

if [[ $# -lt 1 ]]; then
  print_help
  exit 1
fi

command="$1"
target="${2:-}"

case "${command}" in
  red|green|refactor)
    if [[ -z "${target}" ]]; then
      echo "pytest node is required for '${command}'" >&2
      print_help
      exit 2
    fi
    pytest "${target}" -q
    ;;
  gate)
    pytest tests/ -q
    ruff check .
    ruff format --check .
    ;;
  *)
    echo "Unknown command: ${command}" >&2
    print_help
    exit 2
    ;;
esac
