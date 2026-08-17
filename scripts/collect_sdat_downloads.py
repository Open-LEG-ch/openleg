#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Vom Datahub heruntergeladene SDAT-Archive einsammeln.

Der Datahub gibt Nachrichten im Benachrichtigungs-Archiv als ``*.xml.gz`` aus.
``import_sdat.py`` liest diese Archive direkt. Dieses Skript prüft und kopiert
alles, was Chrome in den Download-Ordner gelegt hat, ins Importverzeichnis.

Ablauf:
    1. ``scripts/datahub_download_notifications.js`` in der Chrome-Konsole
       laufen lassen (lädt alle Benachrichtigungen herunter)
    2. python scripts/collect_sdat_downloads.py
    3. python scripts/import_sdat.py data/sdat --dry-run

Aufruf:
    python scripts/collect_sdat_downloads.py
    python scripts/collect_sdat_downloads.py --source ~/Downloads --dest data/sdat
    python scripts/collect_sdat_downloads.py --list
"""

import argparse
import gzip
import logging
import os
import re
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_SOURCE = "~/Downloads"
DEFAULT_DEST = "data/sdat"
ARCHIVE_SUFFIX = ".gz"
# Chrome nummeriert Doppeldownloads: "....xml (1).gz".
DUPLICATE_COUNTER = re.compile(r" \(\d+\)$")


def _is_archive(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith(ARCHIVE_SUFFIX) and ".xml" in lowered


def output_name(archive_name: str) -> str:
    """``foo.xml.gz`` and ``foo.xml (1).gz`` both map to ``foo.xml.gz``."""
    stem = archive_name[: -len(ARCHIVE_SUFFIX)]
    return DUPLICATE_COUNTER.sub("", stem) + ARCHIVE_SUFFIX


class CollectError(RuntimeError):
    """The source directory is missing or unreadable."""


def collect(
    source, dest, *, force: bool = False, dry_run: bool = False, move: bool = False
) -> dict:
    """Validate and copy every ``*.xml.gz`` in ``source`` into ``dest``.

    Args:
        source: directory Chrome downloaded the archives into.
        dest: municipality data directory the importer reads.
        force: rewrite outputs that already exist.
        dry_run: report what would happen, write nothing.
        move: delete the source archive after it was copied successfully.

    Returns:
        Summary with the keys ``pending``, ``written``, ``skipped`` and
        ``failed``.
    """
    source = Path(source).expanduser()
    dest = Path(dest).expanduser()

    if not source.is_dir():
        raise CollectError(f"Download-Verzeichnis fehlt: {source}")

    archives = sorted(
        path for path in source.iterdir() if path.is_file() and _is_archive(path.name)
    )

    summary: dict = {
        "source": str(source),
        "dest": str(dest),
        "pending": [],
        "written": [],
        "skipped": [],
        "failed": [],
    }

    grouped = {}
    for archive in archives:
        target = dest / output_name(archive.name)
        grouped.setdefault(target, []).append(archive)

    todo = []
    for target, candidates in grouped.items():
        if target.exists() and not force:
            summary["skipped"].append(target.name)
            continue
        # Das Original hat Vorrang vor Chromes nummerierten Doppeldownloads.
        # Ist es beschädigt, probieren wir die übrigen Kopien statt die ganze
        # Lieferung zu verlieren.
        candidates.sort(
            key=lambda path: (
                bool(DUPLICATE_COUNTER.search(path.name[: -len(ARCHIVE_SUFFIX)])),
                path.name,
            )
        )
        todo.append((candidates, target))

    summary["pending"] = [candidates[0].name for candidates, _ in todo]
    if dry_run:
        return summary

    for candidates, target in todo:
        archive = None
        invalid = []
        for candidate in candidates:
            try:
                _copy_validated_archive(candidate, target)
            except (OSError, EOFError, gzip.BadGzipFile) as exc:
                invalid.append(candidate.name)
                logger.warning(
                    "[SDAT] %s ist kein gültiges Archiv: %s", candidate.name, exc
                )
                continue
            archive = candidate
            break
        if archive is None:
            summary["failed"].extend(invalid)
            continue

        summary["written"].append(target.name)
        logger.info("[SDAT] Eingesammelt: %s", target.name)
        if move and not archive.samefile(target):
            archive.unlink(missing_ok=True)

    summary["pending"] = []
    return summary


def _copy_validated_archive(archive: Path, target: Path) -> None:
    """Copy raw archive bytes atomically after validating the full gzip stream."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    try:
        shutil.copyfile(archive, partial)
        with gzip.open(partial, "rb") as archive_handle:
            while archive_handle.read(1024 * 1024):
                pass
        os.replace(partial, target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE,
        help=f"Download-Ordner (Standard: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--dest",
        default=DEFAULT_DEST,
        help=f"Zielverzeichnis (Standard: {DEFAULT_DEST})",
    )
    parser.add_argument(
        "--force", action="store_true", help="Vorhandene Archive überschreiben"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="dry_run",
        help="Nur anzeigen, was eingesammelt würde",
    )
    parser.add_argument(
        "--move",
        action="store_true",
        help="Archiv nach erfolgreichem Kopieren aus dem Download-Ordner löschen",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        result = collect(
            args.source,
            args.dest,
            force=args.force,
            dry_run=args.dry_run,
            move=args.move,
        )
    except CollectError as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.dry_run:
        for name in result["pending"]:
            print(f"  würde einsammeln: {name}")
        print(
            f"Offen: {len(result['pending'])}, "
            f"bereits vorhanden: {len(result['skipped'])}"
        )
        return 0

    print(
        f"Eingesammelt: {len(result['written'])} nach {result['dest']}, "
        f"übersprungen: {len(result['skipped'])}, "
        f"fehlgeschlagen: {len(result['failed'])}"
    )
    for name in result["failed"]:
        print(f"  FEHLER: {name}", file=sys.stderr)

    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
