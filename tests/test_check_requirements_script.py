# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for scripts/check_requirements.py."""

import importlib.util
import os
import subprocess
import sys
from importlib import metadata

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(PROJECT_ROOT, "scripts", "check_requirements.py")

_spec = importlib.util.spec_from_file_location("check_requirements", SCRIPT_PATH)
check_requirements = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_requirements)

PYTEST_VERSION = metadata.version("pytest")


def run_check(path):
    return subprocess.run(
        [sys.executable, SCRIPT_PATH, str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_only_plain_pins_are_read():
    text = """# Kommentar

Flask==3.1.0  # Web
psycopg[binary]==3.2.0
tomli==2.0.1; python_version < '3.11'
requests>=2.0
-r other.txt
"""
    assert check_requirements.pinned_requirements(text) == [("Flask", "3.1.0")]


def test_matching_environment_passes(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text(f"pytest=={PYTEST_VERSION}\n")

    result = run_check(req)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_names_are_normalized(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text(f"PyTest=={PYTEST_VERSION}\n")

    assert run_check(req).returncode == 0


def test_missing_package_is_reported(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("surely-not-installed-openleg-pkg==1.0\n")

    result = run_check(req)

    assert result.returncode == 1
    assert "surely-not-installed-openleg-pkg==1.0 (fehlt)" in result.stderr


def test_version_mismatch_is_reported(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("pytest==0.0.1\n")

    result = run_check(req)

    assert result.returncode == 1
    assert f"pytest==0.0.1 (installiert: {PYTEST_VERSION})" in result.stderr
