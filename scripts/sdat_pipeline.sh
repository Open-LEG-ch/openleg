#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# SDAT Dateien vom Datahub holen, einsammeln und importieren.
#
# Führt die drei Schritte nacheinander aus:
#   1. Neue Dateien vom Swisseldex Datahub nach data/<gemeinde> laden
#   2. Archive aus ~/Downloads nach data/<gemeinde> einsammeln
#   3. Die Dateien in data/<gemeinde> importieren
#
# import_sdat.py liest .xml.gz direkt, darum wird das Gemeindeverzeichnis nicht
# mehr entpackt. Früher lief hier ein zusätzlicher Schritt, der jedes Archiv
# im Verzeichnis auspackte, damit der Import es danach wieder einpackte: bei
# einem Archiv aus einem Jahr Lieferungen reine Leerarbeit.
#
# Schritt 1 braucht die Zugangsdaten SWISSELDEX_FTPS_USER und
# SWISSELDEX_FTPS_PASSWORD in .env. Schlägt er fehl, warnt die Pipeline und
# arbeitet mit den lokal vorhandenen Dateien weiter. Mit --no-fetch lässt sich
# der Abruf ganz überspringen, zum Beispiel für die Arbeit offline.
#
# Aufruf:
#   scripts/sdat_pipeline.sh
#   scripts/sdat_pipeline.sh data/sdat
#   scripts/sdat_pipeline.sh data/sdat --dry-run
#   scripts/sdat_pipeline.sh --no-fetch

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_DEST="data/sdat"

print_help() {
  cat <<'EOF'
Usage: scripts/sdat_pipeline.sh [dest] [--no-fetch] [import-args...]

Arguments:
  dest          Gemeindeverzeichnis (Standard: data/sdat)
  --no-fetch    Schritt 1 überspringen, nichts vom Datahub laden
  import-args   Weitere Argumente für import_sdat.py (z.B. --dry-run, --force)

Steps:
  1  fetch_sdat.py --out <dest>                                 (--no-fetch überspringt)
  2  collect_sdat_downloads.py --dest <dest>
  3  import_sdat.py <dest> [import-args...]

Der Import liest .xml.gz direkt und überspringt Dateien, die schon in der
Datenbank liegen.

Schritt 1 braucht SWISSELDEX_FTPS_USER und SWISSELDEX_FTPS_PASSWORD in .env.
Ein fehlgeschlagener Abruf warnt nur, die Pipeline importiert danach die lokal
vorhandenen Dateien.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  print_help
  exit 0
fi

# --no-fetch gilt an jeder Position und darf nicht an import_sdat.py gehen.
FETCH=1
REST=()
for arg in "$@"; do
  case "$arg" in
    --no-fetch) FETCH=0 ;;
    *) REST+=("$arg") ;;
  esac
done
set -- ${REST[@]+"${REST[@]}"}

DEST="$DEFAULT_DEST"
if [[ $# -gt 0 && "$1" != -* ]]; then
  DEST="$1"
  shift
fi

# Alle Schritte laufen mit dem venv-Interpreter, damit kein
# "source .venv/bin/activate" nötig ist. PYTHON lässt sich für Tests
# überschreiben.
PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  echo "Virtualenv fehlt: $PYTHON" >&2
  echo "Anlegen mit: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 2
fi

cd "$REPO_ROOT"

if [[ "$FETCH" -eq 1 ]]; then
  echo "==> 1/3 Datahub abrufen nach $DEST"
  if ! "$PYTHON" scripts/fetch_sdat.py --out "$DEST"; then
    echo "Warnung: Datahub-Abruf fehlgeschlagen, arbeite mit lokalen Dateien weiter." >&2
    echo "Warnung: Zugangsdaten in .env prüfen oder mit --no-fetch aufrufen." >&2
  fi
else
  echo "==> 1/3 Datahub abrufen übersprungen (--no-fetch)"
fi

echo "==> 2/3 Downloads einsammeln nach $DEST"
if ! "$PYTHON" scripts/collect_sdat_downloads.py --dest "$DEST"; then
  echo "Warnung: Einsammeln fehlgeschlagen, importiere vorhandene Dateien aus $DEST." >&2
fi

echo "==> 3/3 Import aus $DEST"
"$PYTHON" scripts/import_sdat.py "$DEST" "$@"
