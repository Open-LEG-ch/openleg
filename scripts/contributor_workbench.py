#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contributor workbench command-line interface."""

from __future__ import annotations

import argparse
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("doctor")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "doctor":
        repo_root = Path(__file__).resolve().parents[1]
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
