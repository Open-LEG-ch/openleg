#!/usr/bin/env bash
# Shared local/CI gate. Keep the explicit scope in mypy-strict.ini.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
py_cmd="$(command -v python || command -v python3)"
required_version="$(sed -n 's/^mypy==//p' requirements-dev.txt)"
found_version="$("$py_cmd" -m mypy --version 2>/dev/null || true)"
if [[ "$found_version" != "mypy $required_version "* ]]; then
  printf 'mypy %s required; found %s. Install requirements-dev.txt\n' "$required_version" "${found_version:-missing}" >&2
  exit 2
fi
exec "$py_cmd" -m mypy --config-file mypy-strict.ini
