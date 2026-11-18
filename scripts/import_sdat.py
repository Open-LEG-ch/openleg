# SPDX-License-Identifier: AGPL-3.0-or-later
"""SDAT E66 Messdaten importieren.

Liest ebIX E66 Dateien (ValidatedMeteredData_16) und schreibt sie idempotent in
metering_point_readings. Bereits importierte Dokumente werden übersprungen,
E31 Geschwisterdateien im selben Verzeichnis ebenso.

Sobald die Zeilen in der Datenbank liegen, packt der Import die XML-Datei
wieder als ``.gz`` und löscht das Original. Die entpackten Dateien machen den
Grossteil von ``data/`` aus und werden nach dem Import nicht mehr gebraucht.
``--no-compress`` schaltet das ab. ``.xml.gz`` liest der Import direkt, damit
``--force`` auch nach dem Packen noch funktioniert.

Den Import selbst macht ``sdat_import.import_paths``, wie beim geplanten Import.
Dieses Skript liest dessen Ergebnis und formatiert Ausgabe und Exit-Code.

Aufruf:
    python scripts/import_sdat.py data/sdat
    python scripts/import_sdat.py data/sdat --dry-run
    python scripts/import_sdat.py data/sdat --force
    python scripts/import_sdat.py data/sdat --no-compress
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sdat_e66
import sdat_import

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Diagnostic texts. ``{name}`` is the file, ``{detail}`` what the import saw.
_MESSAGES = {
    sdat_import.MISSING_PATH: "Fehler: Pfad nicht gefunden: {detail}",
    sdat_import.STORAGE_UNAVAILABLE: "DATABASE_URL fehlt oder DB nicht erreichbar.",
    sdat_import.LEDGER_UNREADABLE: (
        "Warnung: Import-Ledger nicht lesbar, alle Dateien werden vollständig geprüft"
    ),
    sdat_import.UNREADABLE: "  Fehler: Datei nicht lesbar ({detail})",
    sdat_import.INVALID_DOCUMENT: "  Fehler: {detail}",
    sdat_import.INCOMPLETE_PERSISTENCE: (
        "  Fehler: Import unvollständig, Datei bleibt liegen"
    ),
    sdat_import.LEDGER_NOT_RECORDED: (
        "  Fehler: Import nicht im Ledger vermerkt, Datei bleibt liegen"
    ),
    sdat_import.VERACITY_FLAGS_NOT_RECORDED: "  Warnung: Veracity-Flags nicht vermerkt",
    sdat_import.COMPRESSION_FAILED: "  Warnung: {name} nicht gepackt ({detail})",
}


def _message(diagnostic, name=""):
    return _MESSAGES[diagnostic.code].format(name=name, detail=diagnostic.detail)


def _initialize_database():
    """Open the connection pool. Imported here: loading the script stays DB-free."""
    import database

    return database.init_db()


def _report_document(document, readings, quiet):
    print(f"  Zeitraum: {document.period_start} bis {document.period_end}")
    print(
        f"  Messpunkte {document.point_count}   "
        f"Kanäle {document.block_count}   "
        f"Zeilen {document.row_count}"
    )
    if readings is not None:
        print(
            f"  neu {readings.new}   korrigiert {readings.corrected}   "
            f"unverändert {readings.unchanged}"
        )
        if readings.samples and not quiet:
            for point, direction, measured_at in readings.samples[:3]:
                print(
                    f"    Korrektur: {sdat_e66.mask_point_id(point)} "
                    f"{direction} {measured_at}"
                )
    if not quiet:
        for warning in document.warnings:
            print(f"  Warnung: {warning}")
    if document.veracity_flag_count:
        print(f"  Veracity-Flags: {document.veracity_flag_count}")


def _report_file(outcome, quiet):
    """One file's lines, in the order the import settled them."""
    document = outcome.document
    if document is not None:
        print(f"{outcome.name}   {document.doc_type} {document.document_id}")
    elif outcome.status == sdat_import.FAILED:
        print(outcome.name)
    packing = sdat_import.COMPRESSION_FAILED
    for item in outcome.diagnostics:
        if item.code != packing:
            print(_message(item, outcome.name))
    if document is not None and outcome.status == sdat_import.ALREADY:
        print("  übersprungen (bereits importiert)")
    if document is not None and outcome.status == sdat_import.IMPORTED:
        _report_document(document, outcome.readings, quiet)
    for item in outcome.diagnostics:
        if item.code == packing:
            print(_message(item, outcome.name))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="SDAT E66 Messdaten importieren",
    )
    parser.add_argument("paths", nargs="+", help="Dateien oder Verzeichnisse")
    parser.add_argument(
        "--force",
        action="store_true",
        help="bereits importierte Dokumente erneut lesen",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="nur prüfen, nichts schreiben"
    )
    parser.add_argument("--quiet", action="store_true", help="nur die Zusammenfassung")
    parser.add_argument(
        "--no-compress",
        dest="compress",
        action="store_false",
        help="die XML-Datei nach dem Import nicht als .gz packen",
    )
    args = parser.parse_args(argv)

    def reporter(event):
        if isinstance(event, sdat_import.FileOutcome):
            _report_file(event, args.quiet)
        else:
            print(_message(event))

    report = sdat_import.import_paths(
        args.paths,
        dry_run=args.dry_run,
        force=args.force,
        compress=args.compress,
        initialize=_initialize_database,
        reporter=reporter,
    )
    if report.storage_unavailable:
        return 1

    print(
        f"\nDateien: {report.count(sdat_import.IMPORTED)} verarbeitet, "
        f"{report.count(sdat_import.ALREADY)} bereits importiert, "
        f"{report.count(sdat_import.SKIPPED)} übersprungen, "
        f"{report.count(sdat_import.FAILED)} fehlerhaft"
    )
    if not args.dry_run:
        totals = report.readings
        print(
            f"Zeilen: neu {totals.new}, korrigiert {totals.corrected}, "
            f"unverändert {totals.unchanged}"
        )
    return 0 if report.succeeded else 1


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    raise SystemExit(main())
