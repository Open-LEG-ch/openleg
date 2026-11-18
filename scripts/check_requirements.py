#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Prüfen, ob der laufende Interpreter alle fest gepinnten Abhängigkeiten hat.

Liest die Zeilen ``name==version`` aus einer requirements-Datei und vergleicht
sie mit den installierten Paketen. Fehlt ein Paket oder weicht die Version ab,
listet das Skript die Abweichungen auf und endet mit Code 1.

So bricht ein veraltetes .venv ab, bevor die Arbeit beginnt, statt mitten im
Import mit ModuleNotFoundError.

Das Skript überspringt Zeilen mit Markern, Extras, Platzhaltern wie ``1.*``,
mehreren Bedingungen oder ohne ``==``. Versionen vergleicht es nach PEP 440 wie
pip: ``==1.0`` passt auch zu ``1.0.0`` und ``1.0+lokal``.

Aufruf:
    .venv/bin/python scripts/check_requirements.py
    .venv/bin/python scripts/check_requirements.py requirements.txt
"""

import re
import sys
from importlib import metadata
from pathlib import Path

try:
    from packaging.specifiers import InvalidSpecifier, Specifier
    from packaging.version import InvalidVersion
except ImportError:  # Ohne packaging bleibt der exakte Textvergleich.
    Specifier = None

DEFAULT_REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements.txt"
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;#\[*,]+)$")


def _normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _satisfies(installed, wanted):
    if Specifier is not None:
        try:
            return Specifier(f"=={wanted}").contains(installed, prereleases=True)
        except (InvalidSpecifier, InvalidVersion):
            pass
    return installed == wanted


def pinned_requirements(text):
    """Liefert (name, version) für jede einfache ``name==version``-Zeile."""
    pins = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        match = PIN.match(line)
        if match:
            pins.append((match.group(1), match.group(2)))
    return pins


def mismatches(pins):
    """Liefert lesbare Einträge für fehlende oder abweichende Pakete."""
    problems = []
    for name, wanted in pins:
        try:
            installed = metadata.version(_normalize(name))
        except metadata.PackageNotFoundError:
            problems.append(f"{name}=={wanted} (fehlt)")
            continue
        if not _satisfies(installed, wanted):
            problems.append(f"{name}=={wanted} (installiert: {installed})")
    return problems


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0]) if args else DEFAULT_REQUIREMENTS
    problems = mismatches(pinned_requirements(path.read_text(encoding="utf-8")))
    if not problems:
        return 0
    print(f"Virtualenv passt nicht zu {path.name}:", file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
