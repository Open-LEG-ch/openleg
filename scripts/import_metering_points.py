# SPDX-License-Identifier: AGPL-3.0-or-later
"""Messpunkt-Register aus einer Teilnehmerliste anreichern.

Der SDAT Import legt Messpunkte selbst an, kennt aber weder Gebäude noch
Adresse. Diese Zuordnung kommt aus der Teilnehmerliste des Betreibers, als CSV
exportiert (LibreOffice: Datei, Speichern unter, Text CSV).

Erwartete Kopfzeile:
    messpunktnummer,alias,adresse,building_id,community_id

Leere Felder überschreiben nichts. Die CSV enthält Personendaten und gehört
unter data/, das nicht versioniert wird.

Aufruf:
    python scripts/import_metering_points.py data/sdat/teilnehmer.csv
    python scripts/import_metering_points.py data/... --dry-run
"""

import argparse
import csv
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

# Muss vor dem database-Import laufen: database.py liest DATABASE_URL beim
# Import und fällt sonst still auf die JSON-Ablage zurück.
load_dotenv()

import database as db
import sdat_e66

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REQUIRED_COLUMN = "messpunktnummer"
OPTIONAL_COLUMNS = ("alias", "adresse", "building_id", "community_id")
SUPPORTED_COLUMNS = {REQUIRED_COLUMN, *OPTIONAL_COLUMNS}
# Schlüssel, unter dem überzählige Felder einer Zeile landen.
SURPLUS_KEY = "__surplus__"


def _normalise(row):
    """Spaltennamen kleinschreiben und Werte trimmen.

    Überzählige Felder sammelt ``csv`` unter einem Schlüssel als Liste. Die
    landet hier als Liste und nicht als Text, darum wird sie erkannt und nicht
    getrimmt.
    """
    clean = {}
    for key, value in row.items():
        name = (key or "").strip().lower()
        if isinstance(value, list):
            clean[SURPLUS_KEY] = [str(item).strip() for item in value]
            continue
        clean[name] = (value or "").strip()
    return clean


def _read_points(path):
    """CSV lesen. Gibt (points, errors) zurück."""
    points, errors = [], []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        headers = [(name or "").strip().lower() for name in (reader.fieldnames or [])]
        if REQUIRED_COLUMN not in headers:
            return [], [
                f"Spalte {REQUIRED_COLUMN} fehlt. Gefunden: {', '.join(headers)}"
            ]
        duplicates = sorted(
            {
                name
                for name in headers
                if name in SUPPORTED_COLUMNS and headers.count(name) > 1
            }
        )
        if duplicates:
            return [], [f"Doppelte Spalten: {', '.join(duplicates)}"]
        unsupported = sorted(set(headers) - SUPPORTED_COLUMNS)
        if unsupported:
            logger.warning("Nicht unterstützte Spalten: %s", ", ".join(unsupported))

        for number, raw in enumerate(reader, start=2):
            row = _normalise(raw)
            if row.get(SURPLUS_KEY):
                # Meist ein Komma im Adressfeld ohne Anführungszeichen. Die
                # Spalten sind dann verschoben, und diese Zeile entscheidet, wem
                # ein Messpunkt zugerechnet wird. Darum nicht raten, sondern
                # melden und die Zeile auslassen.
                errors.append(
                    f"Zeile {number}: mehr Spalten als Kopfzeile, übersprungen. "
                    "Komma im Feld? Dann das Feld in Anführungszeichen setzen."
                )
                continue
            point_id = row.get(REQUIRED_COLUMN, "")
            if not point_id:
                errors.append(f"Zeile {number}: ohne Messpunktnummer, übersprungen.")
                continue
            points.append(
                {
                    "metering_point_id": point_id,
                    "alias": row.get("alias") or None,
                    "address": row.get("adresse") or None,
                    "building_id": row.get("building_id") or None,
                    "community_id": row.get("community_id") or None,
                }
            )
    return points, errors


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Messpunkt-Register aus einer Teilnehmerliste anreichern",
    )
    parser.add_argument("path", help="CSV mit der Teilnehmerliste")
    parser.add_argument(
        "--dry-run", action="store_true", help="nur prüfen, nichts schreiben"
    )
    args = parser.parse_args(argv)

    if not os.path.isfile(args.path):
        print(f"Fehler: Datei nicht gefunden: {args.path}")
        return 1

    try:
        points, errors = _read_points(args.path)
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        print(f"Fehler: CSV nicht lesbar ({e})")
        return 1

    for error in errors:
        print(f"Warnung: {error}")

    print(f"Messpunkte in der Datei: {len(points)}")
    for point in points:
        masked = sdat_e66.mask_point_id(point["metering_point_id"])
        filled = [name for name in OPTIONAL_COLUMNS if point.get(_field(name))]
        print(f"  {masked}   Felder: {', '.join(filled) or 'keine'}")

    if args.dry_run:
        return 1 if errors else 0

    if not db.init_db():
        print("DATABASE_URL fehlt oder DB nicht erreichbar.")
        return 1

    written = db.upsert_metering_points(points)
    print(f"Register aktualisiert: {written} Messpunkte")
    return 1 if errors else 0


def _field(csv_column):
    """CSV-Spalte auf den Feldnamen im Register abbilden."""
    return {"adresse": "address"}.get(csv_column, csv_column)


if __name__ == "__main__":
    raise SystemExit(main())
