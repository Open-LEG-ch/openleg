#!/usr/bin/env bash
# Deterministic TDD cycle runner for pytest-first slices.
# Usage:
#   scripts/tdd_cycle.sh red <pytest-node>
#   scripts/tdd_cycle.sh green <pytest-node>
#   scripts/tdd_cycle.sh refactor <pytest-node>
#   scripts/tdd_cycle.sh gate

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"

python_cmd() {
  if command -v python >/dev/null 2>&1; then
    printf 'python\n'
    return 0
  fi

  if command -v python3 >/dev/null 2>&1; then
    printf 'python3\n'
    return 0
  fi

  return 1
}

require_pinned_ruff() {
  local requirements_file="${repo_root}/requirements-dev.txt"
  local required_version
  local ruff_output
  local found_version
  local py_cmd

  required_version="$(
    awk -F'==' '
      /^ruff==/ { count++; version=$2 }
      END {
        if (count == 1 && version != "") {
          print version
        }
      }
    ' "${requirements_file}"
  )"

  py_cmd="$(python_cmd || true)"
  if [[ -z "${py_cmd}" ]]; then
    printf 'Ruff %s required; found missing\n' "${required_version:-unknown}" >&2
    printf 'Install Python first, then run pip install -r requirements-dev.txt\n' >&2
    exit 2
  fi

  ruff_output="$("${py_cmd}" -m ruff --version 2>&1 || true)"
  found_version="$(printf '%s\n' "${ruff_output}" | awk '/^ruff / { print $2; exit }')"

  if [[ -z "${required_version}" || -z "${found_version}" || "${found_version}" != "${required_version}" ]]; then
    printf 'Ruff %s required; found %s\n' "${required_version:-unknown}" "${found_version:-missing}" >&2
    printf '%s -m pip install -r requirements-dev.txt\n' "${py_cmd}" >&2
    exit 2
  fi
}

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
    py_cmd="$(python_cmd || true)"
    require_pinned_ruff
    pytest tests/ -q
    "${py_cmd}" -m ruff check .
    "${py_cmd}" -m ruff format --check .
    ;;
  *)
    echo "Unknown command: ${command}" >&2
    print_help
    exit 2
    ;;
esac
