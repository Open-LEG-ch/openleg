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

Aufruf:
    python scripts/import_sdat.py data/sdat
    python scripts/import_sdat.py data/sdat --dry-run
    python scripts/import_sdat.py data/sdat --force
    python scripts/import_sdat.py data/sdat --no-compress
"""

import argparse
import gzip
import logging
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sdat_e66

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Lazy database handle: tests inject a fake before calling main().
db = None


def _database():
    """Import database lazily, once, and return the module."""
    global db
    if db is None:
        import database

        db = database
    return db


GZ_SUFFIX = ".gz"


def _is_sdat_file(name):
    return name.lower().endswith((".xml", ".xml" + GZ_SUFFIX))


def _plain_name(name):
    """``foo.xml.gz`` heisst im Bericht und im Ledger ``foo.xml``.

    Die Kompression ist Verpackung, nicht Identität: dieselbe Lieferung soll
    gepackt und entpackt unter demselben Namen auftauchen.
    """
    return name[: -len(GZ_SUFFIX)] if name.lower().endswith(GZ_SUFFIX) else name


def _candidate_files(paths):
    """Dateien einsammeln; Verzeichnisse nach *.xml und *.xml.gz durchsuchen."""
    files, missing = [], []
    for raw in paths:
        if os.path.isdir(raw):
            for name in sorted(os.listdir(raw)):
                if _is_sdat_file(name):
                    files.append(os.path.join(raw, name))
        elif os.path.isfile(raw):
            files.append(raw)
        else:
            missing.append(raw)
    return files, missing


def _read_text(path):
    """Inhalt lesen, ``.gz`` transparent entpacken."""
    if path.lower().endswith(GZ_SUFFIX):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return handle.read()
    with open(path, encoding="utf-8") as handle:
        return handle.read()


# Reicht für Dokumenttyp und DocumentID: im Beispiel steht die ID bei Zeichen
# 1369. Grosszügig gewählt, damit auch ein langer Kopf noch hineinpasst; gegen
# einen vollen Parse ist das nichts.
HEAD_LIMIT = 16384


def _read_head(path, limit=HEAD_LIMIT):
    """Nur den Anfang lesen, um Typ und ID zu bestimmen.

    Bei ``.gz`` entpackt das nur die ersten Blöcke, nicht die ganze Datei.
    """
    if path.lower().endswith(GZ_SUFFIX):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return handle.read(limit)
    with open(path, encoding="utf-8") as handle:
        return handle.read(limit)


def compress_imported_file(path):
    """Die importierte XML-Datei als ``.gz`` ablegen und das Original löschen.

    Gibt den Pfad des Archivs zurück, oder ``None`` wenn die Datei schon gepackt
    ist. Erst schreiben, dann prüfen, dann löschen: die Datei ist die einzige
    lokale Kopie der Lieferung, darum verschwindet sie nur gegen ein Archiv, das
    sich nachweislich wieder auspacken lässt.
    """
    path = Path(path)
    if path.name.lower().endswith(GZ_SUFFIX):
        return None

    target = path.with_name(path.name + GZ_SUFFIX)
    if target.exists():
        raise FileExistsError(f"Archiv existiert bereits: {target.name}")
    partial = target.with_name(target.name + ".part")
    original_size = path.stat().st_size
    try:
        with open(path, "rb") as source, gzip.open(partial, "wb") as sink:
            shutil.copyfileobj(source, sink)
        written = 0
        with gzip.open(partial, "rb") as handle:
            while chunk := handle.read(1024 * 1024):
                written += len(chunk)
        if written != original_size:
            raise OSError(
                f"Archiv unvollständig: {target.name} "
                f"({written} statt {original_size} Bytes)"
            )
        # Hardlink statt replace: existiert das Ziel seit der Prüfung oben,
        # schlägt das atomar fehl und keine der beiden Lieferungen geht verloren.
        os.link(partial, target)
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        partial.unlink(missing_ok=True)
        if isinstance(exc, FileExistsError):
            raise FileExistsError(f"Archiv existiert bereits: {target.name}") from exc
        raise
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    partial.unlink(missing_ok=True)
    path.unlink(missing_ok=True)
    return target


def _sort_key(path):
    """Nach dem chronologisch aufgebauten Basisdateinamen sortieren.

    Last-write-wins stimmt nur, wenn ältere Dokumente zuerst laufen. Der
    Dateiname beginnt mit YYYYMMDD_HHMMSS und sortiert daher chronologisch.
    """
    return os.path.basename(path)


def _report(document, result, quiet):
    period_start = document.get("period_start")
    period_end = document.get("period_end")
    print(f"  Zeitraum: {period_start} bis {period_end}")
    print(
        f"  Messpunkte {len(document.get('point_ids', []))}   "
        f"Kanäle {document.get('block_count', 0)}   "
        f"Zeilen {len(document.get('rows', []))}"
    )
    if result is not None:
        print(
            f"  neu {result['new']}   korrigiert {result['corrected']}   "
            f"unverändert {result['unchanged']}"
        )
        if result["samples"] and not quiet:
            for point, direction, measured_at in result["samples"][:3]:
                print(
                    f"    Korrektur: {sdat_e66.mask_point_id(point)} "
                    f"{direction} {measured_at}"
                )
    if not quiet:
        for warning in document.get("warnings", []):
            print(f"  Warnung: {warning}")


def _compress(path, args, name):
    """Nach dem Import packen. Ein Fehler kostet Platz, nicht die Daten."""
    if args.dry_run or not getattr(args, "compress", True):
        return
    try:
        compress_imported_file(path)
    except (OSError, EOFError, gzip.BadGzipFile) as e:
        print(f"  Warnung: {name} nicht gepackt ({e})")


def _already_imported(document_id, args, index):
    """Liegt dieses Dokument schon im Ledger?

    Mit Index eine Mengenabfrage, ohne Index die alte Einzelabfrage. ``--force``
    ignoriert beides.
    """
    if args.force or not document_id:
        return False
    if index is not None:
        return document_id in index["document_ids"]
    return bool(_database().get_sdat_import(document_id))


def _import_file(path, args, index=None):
    """Eine Datei verarbeiten. Gibt (status, result) zurück.

    Die billigen Entscheidungen kommen zuerst: erst der Kopf, dann die ID, und
    nur für eine wirklich neue Lieferung die ganze Datei. Der volle Parse ist
    rund 2500 mal teurer als der Kopf-Scan und wächst mit der Dateigrösse.
    """
    name = _plain_name(os.path.basename(path))
    try:
        head = _read_head(path)
    except (OSError, EOFError, gzip.BadGzipFile, UnicodeDecodeError) as e:
        print(f"{name}\n  Fehler: Datei nicht lesbar ({e})")
        return "failed", None

    if not sdat_e66.is_e66_document(head):
        # E31 Geschwisterdateien liegen bei jedem Download dabei und sind
        # nichts Besonderes. Sie zählen als übersprungen, bleiben aber still.
        #
        # Ein erkanntes E31 wird gepackt: der Import überspringt es bewusst,
        # niemand liest es noch einmal, und es kommt mit jeder Lieferung mit.
        # Eine sonst unbekannte Datei bleibt dagegen lesbar liegen, denn sie
        # kann ein Lieferproblem sein, das jemand anschauen muss.
        if sdat_e66.is_e31_document(head):
            _compress(path, args, name)
        return "skipped", None

    # Die ID aus dem Kopf ist die Identität. Ist sie nicht lesbar, wird nicht
    # übersprungen, sondern die Datei vollständig verarbeitet.
    if not args.dry_run and _already_imported(
        sdat_e66.extract_document_id(head), args, index
    ):
        _compress(path, args, name)
        return "already", None

    try:
        content = _read_text(path)
    except (OSError, EOFError, gzip.BadGzipFile, UnicodeDecodeError) as e:
        print(f"{name}\n  Fehler: Datei nicht lesbar ({e})")
        return "failed", None

    document, errors = sdat_e66.parse_e66_xml(content)
    if errors:
        print(f"{name}")
        for error in errors:
            print(f"  Fehler: {error}")
        return "failed", None

    document["file_name"] = name
    print(f"{name}   {document['doc_type']} {document['document_id']}")

    if args.dry_run:
        _report(document, None, args.quiet)
        return "imported", None

    # Rückfall: der Kopf gab keine ID her, das Dokument liegt aber schon im
    # Ledger. Jetzt ist die ID aus dem Parse bekannt.
    if _already_imported(document["document_id"], args, index):
        print("  übersprungen (bereits importiert)")
        _compress(path, args, name)
        return "already", None

    db_mod = _database()
    result = db_mod.save_metering_point_readings(
        document["rows"], source_document_id=document["document_id"]
    )
    # Was die Datenbank zurückmeldet, muss der Zeilenzahl aus dem Parse
    # entsprechen. Fehlt auch nur eine Zeile, gilt die Lieferung als nicht
    # gespeichert: kein Ledger-Eintrag, keine Erfolgsmeldung, kein Packen.
    persisted = result["new"] + result["corrected"] + result["unchanged"]
    if persisted != len(document["rows"]):
        print("  Fehler: Import unvollständig, Datei bleibt liegen")
        return "failed", None

    if not db_mod.record_sdat_import(
        {
            **document,
            "row_count": len(document["rows"]),
            "new_count": result["new"],
            "corrected_count": result["corrected"],
        }
    ):
        print("  Fehler: Import nicht im Ledger vermerkt, Datei bleibt liegen")
        return "failed", None

    if index is not None:
        index["document_ids"] = index["document_ids"] | {document["document_id"]}

    _report(document, result, args.quiet)
    _compress(path, args, name)
    return "imported", result


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

    files, missing = _candidate_files(args.paths)
    exit_code = 0
    for path in missing:
        print(f"Fehler: Pfad nicht gefunden: {path}")
        exit_code = 1

    if not args.dry_run and files:
        db_mod = _database()
        if not db_mod.init_db():
            print("DATABASE_URL fehlt oder DB nicht erreichbar.")
            return 1

    # Ein Query pro Lauf statt einer Abfrage pro Datei. Im Dry-Run gar keiner:
    # --dry-run muss ohne Datenbank laufen. Ohne Dateien auch nicht, denn dann
    # lief init_db() oben nicht und es gibt noch keinen Verbindungspool.
    index = None
    if not args.dry_run and files:
        try:
            index = db_mod.get_sdat_import_index()
        except Exception:
            print(
                "Warnung: Import-Ledger nicht lesbar, "
                "alle Dateien werden vollständig geprüft"
            )
            index = {"document_ids": frozenset(), "file_names": frozenset()}

    counts = {"imported": 0, "skipped": 0, "failed": 0, "already": 0}
    totals = {"new": 0, "corrected": 0, "unchanged": 0}

    for path in sorted(files, key=_sort_key):
        status, result = _import_file(path, args, index)
        counts[status] += 1
        if status == "failed":
            exit_code = 1
        if result:
            for key in totals:
                totals[key] += result[key]

    print(
        f"\nDateien: {counts['imported']} verarbeitet, "
        f"{counts['already']} bereits importiert, "
        f"{counts['skipped']} übersprungen, {counts['failed']} fehlerhaft"
    )
    if not args.dry_run:
        print(
            f"Zeilen: neu {totals['new']}, korrigiert {totals['corrected']}, "
            f"unverändert {totals['unchanged']}"
        )
    return exit_code


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    raise SystemExit(main())
