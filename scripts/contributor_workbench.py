#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contributor workbench command-line interface."""

from __future__ import annotations

import argparse
import difflib
import fnmatch
import json
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
VENV_CREATE = "python3 -m venv .venv"
VENV_INSTALL = ".venv/bin/python -m pip install -r requirements-dev.txt"
HEADINGS = {
    GATE: "Required to run the gate",
    CI: "Required to match CI",
    INFO: "Reported only",
}


@dataclass
class Environment:
    python: tuple[int, ...] | None
    pytest: bool
    ruff_executable: str | None
    ruff_module: str | None
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
    if environment.ruff_executable is None:
        detail = "not found"
        if environment.ruff_module is not None:
            detail += (
                f"; python3 -m ruff {environment.ruff_module} works, but "
                "scripts/test.sh requires the executable"
            )
        checks.append(Check(GATE, "ruff executable", False, detail, PIP_INSTALL))
    elif environment.ruff_executable != environment.ruff_pin:
        checks.append(
            Check(
                GATE,
                "ruff executable",
                False,
                f"required {environment.ruff_pin}, found {environment.ruff_executable}",
                PIP_INSTALL,
            )
        )
    else:
        checks.append(
            Check(
                GATE,
                "ruff executable",
                True,
                f"{environment.ruff_executable}, matches pin",
            )
        )
    if (
        environment.ruff_executable is not None
        and environment.ruff_module is not None
        and environment.ruff_executable != environment.ruff_module
    ):
        checks.append(
            Check(
                GATE,
                "ruff sources",
                False,
                f"ruff executable {environment.ruff_executable}; "
                f"python3 -m ruff {environment.ruff_module}; versions disagree",
                PIP_INSTALL,
            )
        )
    if environment.ruff_module is None:
        checks.append(Check(GATE, "python3 -m ruff", False, "not found", PIP_INSTALL))
    elif environment.ruff_module != environment.ruff_pin:
        checks.append(
            Check(
                GATE,
                "python3 -m ruff",
                False,
                f"required {environment.ruff_pin}, found {environment.ruff_module}",
                PIP_INSTALL,
            )
        )
    else:
        checks.append(
            Check(
                GATE,
                "python3 -m ruff",
                True,
                f"{environment.ruff_module}, matches pin",
            )
        )

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


def detect_ruff_executable() -> str | None:
    command = shutil.which("ruff")
    if command is None:
        return None
    try:
        result = subprocess.run(
            [command, "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0 or not result.stdout.startswith("ruff "):
        return None
    return result.stdout.removeprefix("ruff ").strip()


def detect_ruff_module() -> str | None:
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


def render_report_json(report: Report) -> None:
    print(
        json.dumps(
            {
                "checks": [
                    {
                        "group": check.group,
                        "name": check.name,
                        "ok": check.ok,
                        "detail": check.detail,
                    }
                    for check in report.checks
                ]
            }
        )
    )


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


def setup_environment(repo_root: Path, dry_run: bool) -> int:
    if dry_run:
        if not (repo_root / ".venv").exists():
            print(VENV_CREATE)
        print(VENV_INSTALL)
        return 0

    if not (repo_root / ".venv").exists():
        print(VENV_CREATE)
        try:
            result = subprocess.run(
                ["python3", "-m", "venv", ".venv"],
                cwd=repo_root,
                check=False,
            )
        except OSError as error:
            print(f"FAIL setup: virtual environment creation failed: {error}")
            return 1
        if result.returncode != 0:
            print(
                "FAIL setup: virtual environment creation failed with exit code "
                f"{result.returncode}"
            )
            return result.returncode

    print(VENV_INSTALL)
    try:
        result = subprocess.run(
            [".venv/bin/python", "-m", "pip", "install", "-r", "requirements-dev.txt"],
            cwd=repo_root,
            check=False,
        )
    except OSError as error:
        print(f"FAIL setup: install failed: {error}")
        return 1
    if result.returncode != 0:
        print(f"FAIL setup: install failed with exit code {result.returncode}")
        return result.returncode
    print("The current shell is unchanged. Activate the virtual environment now:")
    print("source .venv/bin/activate")
    return 0


def forbidden_path_violations(
    paths: list[str], policy_file: Path
) -> list[tuple[str, str]]:
    patterns = [
        line.strip()
        for line in policy_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    violations = []
    for path in paths:
        pattern = next(
            (
                candidate
                for candidate in patterns
                if fnmatch.fnmatchcase(path, candidate)
            ),
            None,
        )
        if pattern is not None:
            violations.append((path, pattern))
    return violations


def staged_paths(repo_root: Path) -> tuple[list[str] | None, str]:
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        return None, str(error)
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git exited with code {result.returncode}"
        return None, detail
    return result.stdout.splitlines(), ""


def render_check_json(
    violations: list[tuple[str, str]], error: str | None = None
) -> None:
    payload: dict[str, object] = {
        "violations": [
            {"path": path, "pattern": pattern} for path, pattern in violations
        ]
    }
    if error is not None:
        payload["error"] = error
    print(json.dumps(payload))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts/contribute")
    subparsers = parser.add_subparsers(dest="command")
    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--json", action="store_true")
    check = subparsers.add_parser("check")
    check.add_argument("paths", nargs="*")
    check.add_argument("--staged", action="store_true")
    check.add_argument("--json", action="store_true")
    glossary = subparsers.add_parser("glossary")
    glossary.add_argument("term", nargs="?")
    glossary.add_argument("--json", action="store_true")
    subparsers.add_parser("gate")
    test = subparsers.add_parser("test")
    test.add_argument("node")
    test.add_argument("--phase", choices=("red", "green", "refactor"), default="red")
    setup = subparsers.add_parser("setup")
    setup.add_argument("--dry-run", action="store_true")
    tour = subparsers.add_parser("tour")
    tour.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    if args.command == "check":
        paths = list(args.paths)
        if args.staged or not paths:
            found_paths, error = staged_paths(repo_root)
            if found_paths is None:
                detail = f"cannot read staged paths: {error}"
                if args.json:
                    render_check_json([], detail)
                else:
                    print(f"FAIL check: {detail}")
                return 1
            paths.extend(found_paths)
        policy_file = repo_root / ".github" / "forbidden-paths.txt"
        try:
            violations = forbidden_path_violations(paths, policy_file)
        except (OSError, UnicodeError) as error:
            detail = f"cannot read {policy_file}: {error}"
            if args.json:
                render_check_json([], detail)
            else:
                print(f"FAIL check: {detail}")
            return 1
        if args.json:
            render_check_json(violations)
        else:
            for path, pattern in violations:
                print(f"FAIL check: {path} matches forbidden pattern {pattern}")
        return 1 if violations else 0
    if args.command == "setup":
        return setup_environment(repo_root, args.dry_run)
    if args.command == "gate":
        try:
            result = subprocess.run(
                [repo_root / "scripts" / "test.sh", "gate"], check=False
            )
        except OSError as error:
            print(f"FAIL gate: {error}")
            return 1
        return result.returncode
    if args.command == "test":
        try:
            result = subprocess.run(
                [repo_root / "scripts" / "tdd_cycle.sh", args.phase, args.node],
                check=False,
            )
        except OSError as error:
            print(f"FAIL test: {error}")
            return 1
        return result.returncode
    if args.command == "glossary":
        try:
            terms = read_domain_terms(repo_root / "CONTEXT.md")
        except (OSError, ValueError) as error:
            if args.json:
                print(json.dumps({"terms": [], "error": f"CONTEXT.md: {error}"}))
                return 1
            return report_context_error(error)
        selected_terms = terms
        if args.term is None:
            pass
        else:
            terms_by_key = {term.casefold(): term for term in terms}
            matching_term = terms_by_key.get(args.term.casefold())
            if matching_term is None:
                suggestions = difflib.get_close_matches(
                    args.term.casefold(), terms_by_key, n=3, cutoff=0
                )
                suggested_terms = ", ".join(terms_by_key[key] for key in suggestions)
                detail = (
                    f"Unknown glossary term {args.term}. Closest: {suggested_terms}"
                )
                if args.json:
                    print(json.dumps({"terms": [], "error": detail}))
                else:
                    print(detail)
                return 1
            selected_terms = {matching_term: terms[matching_term]}
        if args.json:
            print(
                json.dumps(
                    {
                        "terms": [
                            {"term": term, "meaning": meaning}
                            for term, meaning in selected_terms.items()
                        ]
                    }
                )
            )
        elif args.term is None:
            for term, meaning in terms.items():
                print(f"{term}: {meaning}")
        else:
            print(next(iter(selected_terms.values())))
        return 0
    if args.command == "tour":
        try:
            seams = read_seams(repo_root / "CONTEXT.md")
            store_modules = read_store_modules(repo_root / "CONTEXT.md")
        except (OSError, ValueError) as error:
            if args.json:
                print(
                    json.dumps(
                        {
                            "seams": [],
                            "store_modules": [],
                            "error": f"CONTEXT.md: {error}",
                        }
                    )
                )
                return 1
            return report_context_error(error)
        if args.json:
            print(
                json.dumps(
                    {
                        "entry_points": [
                            {
                                "path": "app.py",
                                "purpose": "application factory and local development server",
                            },
                            {
                                "path": "wsgi.py",
                                "purpose": "production WSGI entry point",
                            },
                            {"path": "api_public.py", "purpose": "public JSON API"},
                        ],
                        "seams": seams,
                        "store_modules": [
                            {"module": module, "purpose": purpose}
                            for module, purpose in store_modules.items()
                        ],
                        "tests": ["tests/: pytest tests and contract tests"],
                        "gate": "scripts/test.sh gate",
                    }
                )
            )
            return 0
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
            report = Report(
                [
                    Check(
                        GATE,
                        "ruff",
                        False,
                        f"cannot read the pin from requirements-dev.txt: {error}",
                        "restore an exact `ruff==<version>` line in requirements-dev.txt",
                    )
                ],
                1,
            )
            if args.json:
                render_report_json(report)
            else:
                render(report)
            return report.exit_code
        report = evaluate(
            Environment(
                python=detect_python(),
                pytest=detect_pytest(),
                ruff_executable=detect_ruff_executable(),
                ruff_module=detect_ruff_module(),
                ruff_pin=ruff_pin,
                node=shutil.which("node") is not None,
                npm=shutil.which("npm") is not None,
                mypy=shutil.which("mypy") is not None,
                venv_exists=venv_exists,
                venv_active=venv_active,
            )
        )
        if args.json:
            render_report_json(report)
        else:
            render(report)
        return report.exit_code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
