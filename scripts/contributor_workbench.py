#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contributor workbench command-line interface."""

from __future__ import annotations

import argparse
import difflib
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

GATE = "gate"
CI = "ci"
INFO = "info"
MINIMUM_PYTHON = (3, 11)
PIP_INSTALL = "python3 -m pip install -r requirements-dev.txt"
HEADINGS = {
    GATE: "Required to run the gate",
    CI: "Required to match CI",
    INFO: "Reported only",
}


@dataclass
class Environment:
    python: tuple[int, ...] | None
    pytest: bool
    ruff: str | None
    ruff_pin: str
    node: bool
    npm: bool
    mypy: bool
    venv_exists: bool
    venv_active: bool


@dataclass
class Check:
    group: str
    name: str
    ok: bool
    detail: str
    fix: str = ""


@dataclass
class Report:
    checks: list[Check]
    exit_code: int

    def of(self, group: str) -> list[Check]:
        return [check for check in self.checks if check.group == group]


def evaluate(environment: Environment) -> Report:
    if environment.python is None:
        python_check = Check(
            GATE,
            "Python",
            False,
            "not found on PATH",
            "install Python 3.11 or newer",
        )
    elif environment.python < MINIMUM_PYTHON:
        found = ".".join(map(str, environment.python))
        python_check = Check(
            GATE,
            "Python",
            False,
            f"found {found}, need >= 3.11",
            "install Python 3.11 or newer",
        )
    else:
        python_check = Check(
            GATE, "Python", True, ".".join(map(str, environment.python))
        )

    checks = [python_check]
    checks.append(
        Check(
            GATE,
            "pytest",
            environment.pytest,
            "importable" if environment.pytest else "not found",
            "" if environment.pytest else PIP_INSTALL,
        )
    )
    if environment.ruff is None:
        checks.append(Check(GATE, "ruff", False, "not found", PIP_INSTALL))
    elif environment.ruff != environment.ruff_pin:
        checks.append(
            Check(
                GATE,
                "ruff",
                False,
                f"required {environment.ruff_pin}, found {environment.ruff}",
                PIP_INSTALL,
            )
        )
    else:
        checks.append(Check(GATE, "ruff", True, f"{environment.ruff}, matches pin"))

    for name, present in (
        ("node", environment.node),
        ("npm", environment.npm),
        ("mypy", environment.mypy),
    ):
        checks.append(
            Check(
                CI,
                name,
                present,
                "present" if present else "not found",
                "" if present else f"CI runs {name}; install it to match CI locally",
            )
        )

    if not environment.venv_exists:
        checks.append(Check(INFO, ".venv", True, "absent"))
    elif not environment.venv_active:
        checks.append(
            Check(
                INFO,
                ".venv",
                True,
                "exists but not on PATH",
                "source .venv/bin/activate",
            )
        )
    else:
        checks.append(Check(INFO, ".venv", True, "active"))

    failed_gate = any(not check.ok for check in checks if check.group == GATE)
    return Report(checks, 1 if failed_gate else 0)


def detect_python() -> tuple[int, ...] | None:
    command = shutil.which("python3")
    if command is None:
        return None
    result = subprocess.run(
        [command, "--version"], capture_output=True, text=True, check=False
    )
    output = f"{result.stdout} {result.stderr}".strip()
    if result.returncode != 0 or not output.startswith("Python "):
        return None
    return tuple(int(part) for part in output.removeprefix("Python ").split("."))


def detect_pytest() -> bool:
    command = shutil.which("python3")
    if command is None:
        return False
    result = subprocess.run(
        [command, "-c", "import pytest"], capture_output=True, check=False
    )
    return result.returncode == 0


def detect_ruff() -> str | None:
    command = shutil.which("python3")
    if command is None:
        return None
    result = subprocess.run(
        [command, "-m", "ruff", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.startswith("ruff "):
        return None
    return result.stdout.removeprefix("ruff ").strip()


def read_ruff_pin(requirements_file: Path) -> str:
    pins = [
        line.removeprefix("ruff==")
        for line in requirements_file.read_text(encoding="utf-8").splitlines()
        if line.startswith("ruff==")
    ]
    if len(pins) != 1 or not pins[0]:
        raise ValueError("requirements-dev.txt must contain one exact ruff pin")
    return pins[0]


def venv_status(repo_root: Path) -> tuple[bool, bool]:
    venv = repo_root / ".venv"
    venv_bin = (venv / "bin").resolve()
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    active = any(Path(entry).resolve() == venv_bin for entry in path_entries if entry)
    return venv.exists(), active


def render(report: Report) -> None:
    for group in (GATE, CI, INFO):
        print(HEADINGS[group])
        for check in report.of(group):
            if group == INFO:
                status = "INFO"
            elif check.ok:
                status = "OK"
            elif group == CI:
                status = "WARNING"
            else:
                status = "FAIL"
            line = f"{status} {check.name}: {check.detail}"
            if check.fix:
                line += f". Next: {check.fix}"
            print(line)


def read_section(context_file: Path, heading: str) -> list[str]:
    lines = context_file.read_text(encoding="utf-8").splitlines()
    marker = f"## {heading}"
    try:
        start = lines.index(marker) + 1
    except ValueError as error:
        raise ValueError(f"missing {marker} section") from error
    end = next(
        (index for index in range(start, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    return lines[start:end]


def read_table(
    context_file: Path, heading: str, expected_headers: tuple[str, str]
) -> list[tuple[str, str]]:
    section = read_section(context_file, heading)
    header = f"| {expected_headers[0]} | {expected_headers[1]} |"
    try:
        header_index = section.index(header)
        separator = section[header_index + 1]
    except (IndexError, ValueError) as error:
        raise ValueError(f"missing or malformed {heading} table") from error
    separator_cells = [cell.strip() for cell in separator.strip("|").split("|")]
    if len(separator_cells) != 2 or any(
        len(cell.strip(":")) < 3 or cell.strip(":-") for cell in separator_cells
    ):
        raise ValueError(f"missing or malformed {heading} table")

    rows = []
    for line in section[header_index + 2 :]:
        if not line.startswith("|"):
            break
        # Split on every delimiter, not the first: a capped split silently
        # folds a stray third column into the meaning.
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 2 or not all(cells):
            raise ValueError(f"missing or malformed {heading} table")
        rows.append((cells[0], cells[1]))
    if not rows:
        raise ValueError(f"missing or malformed {heading} table")
    return rows


def read_domain_terms(context_file: Path) -> dict[str, str]:
    return dict(read_table(context_file, "Domain Terms", ("Term", "Meaning")))


def read_seams(context_file: Path) -> list[str]:
    section = read_section(context_file, "Seams")
    # Require the full `- **`name`**` shape. A truncated line yields an empty
    # name that passes a bare emptiness check and renders as a blank bullet.
    seams = []
    for line in section:
        if not line.startswith("- **`"):
            continue
        rest = line[len("- **`") :]
        name, sep, tail = rest.partition("`")
        if not sep or not name.strip() or not tail.startswith("**"):
            raise ValueError("missing or malformed Seams definitions")
        seams.append(name.strip())
    if not seams:
        raise ValueError("missing or malformed Seams definitions")
    return seams


def read_store_modules(context_file: Path) -> dict[str, str]:
    rows = read_table(context_file, "Module Names", ("Module", "Owns"))
    if any(
        not module.startswith("`store/") or not module.endswith("`")
        for module, _ in rows
    ):
        raise ValueError("missing or malformed store module table")
    return {module.strip("`"): purpose for module, purpose in rows}


def report_context_error(error: Exception) -> int:
    print(f"FAIL CONTEXT.md: {error}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("doctor")
    glossary = subparsers.add_parser("glossary")
    glossary.add_argument("term", nargs="?")
    subparsers.add_parser("tour")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    if args.command == "glossary":
        try:
            terms = read_domain_terms(repo_root / "CONTEXT.md")
        except (OSError, ValueError) as error:
            return report_context_error(error)
        if args.term is None:
            for term, meaning in terms.items():
                print(f"{term}: {meaning}")
        else:
            terms_by_key = {term.casefold(): term for term in terms}
            matching_term = terms_by_key.get(args.term.casefold())
            if matching_term is None:
                suggestions = difflib.get_close_matches(
                    args.term.casefold(), terms_by_key, n=3, cutoff=0
                )
                suggested_terms = ", ".join(terms_by_key[key] for key in suggestions)
                print(f"Unknown glossary term {args.term}. Closest: {suggested_terms}")
                return 1
            print(terms[matching_term])
        return 0
    if args.command == "tour":
        try:
            seams = read_seams(repo_root / "CONTEXT.md")
            store_modules = read_store_modules(repo_root / "CONTEXT.md")
        except (OSError, ValueError) as error:
            return report_context_error(error)
        print("OpenLEG orientation")
        print("Entry points")
        print("- app.py: application factory and local development server")
        print("- wsgi.py: production WSGI entry point")
        print("- api_public.py: public JSON API")
        print("Named seams")
        for seam in seams:
            print(f"- {seam}")
        print("Store modules")
        for module, purpose in store_modules.items():
            print(f"- {module}: {purpose}")
        print("Tests")
        print("- tests/: pytest tests and contract tests")
        print("Gate")
        print("- scripts/test.sh gate")
        return 0
    if args.command == "doctor":
        venv_exists, venv_active = venv_status(repo_root)
        try:
            ruff_pin = read_ruff_pin(repo_root / "requirements-dev.txt")
        except (OSError, ValueError) as error:
            # doctor is what a contributor runs when things are broken, so a
            # broken repository has to be reported, never raised at them.
            print(HEADINGS[GATE])
            print(f"FAIL ruff: cannot read the pin from requirements-dev.txt: {error}")
            print(
                "     restore an exact `ruff==<version>` line in requirements-dev.txt"
            )
            return 1
        report = evaluate(
            Environment(
                python=detect_python(),
                pytest=detect_pytest(),
                ruff=detect_ruff(),
                ruff_pin=ruff_pin,
                node=shutil.which("node") is not None,
                npm=shutil.which("npm") is not None,
                mypy=shutil.which("mypy") is not None,
                venv_exists=venv_exists,
                venv_active=venv_active,
            )
        )
        render(report)
        return report.exit_code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
