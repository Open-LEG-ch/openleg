#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""SDAT-Dateien vom Swisseldex Datahub holen (ftpes://datahub.swisseldex.ch).

Zugangsdaten kommen aus .env (Vorlage: .env.example):
    SWISSELDEX_FTPS_USER, SWISSELDEX_FTPS_PASSWORD

Aufruf:
    python scripts/fetch_sdat.py                  # alle neuen Dateien laden
    python scripts/fetch_sdat.py --list           # nur anzeigen, nichts laden
    python scripts/fetch_sdat.py --since-days 7   # nur die letzte Woche
    python scripts/fetch_sdat.py --limit 20       # nur die 20 neuesten
    python scripts/fetch_sdat.py --pattern '*.xml'
    python scripts/fetch_sdat.py --recursive      # auch Unterverzeichnisse
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

import sdat_datahub


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("muss mindestens 0 sein")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", help="Zielverzeichnis (Standard: SWISSELDEX_SDAT_DIR oder data/sdat)"
    )
    parser.add_argument(
        "--remote-dir", help="Verzeichnis auf dem Datahub (Standard aus .env)"
    )
    parser.add_argument("--pattern", help="Dateimuster, zum Beispiel '*.xml'")
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Auch Unterverzeichnisse durchsuchen (Struktur wird lokal gespiegelt)",
    )
    parser.add_argument(
        "--since-days",
        type=non_negative_int,
        help="Nur Dateien der letzten N Tage laden",
    )
    parser.add_argument(
        "--limit",
        type=non_negative_int,
        help="Nur die N neuesten Dateien laden",
    )
    parser.add_argument(
        "--force", action="store_true", help="Bereits geladene Dateien erneut laden"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="dry_run",
        help="Nur auflisten, was geladen würde",
    )
    parser.add_argument(
        "--delete-remote",
        action="store_true",
        help="Dateien nach erfolgreichem Download auf dem Datahub löschen",
    )
    parser.add_argument("--verbose", action="store_true", help="Debug-Logs anzeigen")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    try:
        config = sdat_datahub.load_config()
    except sdat_datahub.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.out:
        config.local_dir = args.out
    if args.remote_dir:
        config.remote_dir = args.remote_dir

    since = (
        datetime.now(timezone.utc) - timedelta(days=args.since_days)
        if args.since_days is not None
        else None
    )

    try:
        result = sdat_datahub.fetch_latest(
            config,
            since=since,
            limit=args.limit,
            pattern=args.pattern,
            recursive=args.recursive,
            force=args.force,
            dry_run=args.dry_run,
            delete_remote=args.delete_remote,
        )
    except Exception as exc:
        print(f"Datahub-Abruf fehlgeschlagen: {exc}", file=sys.stderr)
        return 1

    print(f"Datahub {config.host}{config.remote_dir}: {result['listed']} Dateien")
    if args.dry_run:
        for name in result["pending"]:
            print(f"  würde laden: {name}")
        print(
            f"Offen: {len(result['pending'])}, bereits vorhanden: {len(result['skipped'])}"
        )
        return 0

    print(
        f"Geladen: {len(result['downloaded'])} "
        f"({result['bytes'] / 1024:.1f} KB) nach {result['local_dir']}, "
        f"übersprungen: {len(result['skipped'])}, "
        f"fehlgeschlagen: {len(result['failed'])}"
    )
    for name in result["failed"]:
        print(f"  FEHLER: {name}", file=sys.stderr)
    if result["deleted"]:
        print(f"Auf dem Datahub gelöscht: {len(result['deleted'])}")

    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
