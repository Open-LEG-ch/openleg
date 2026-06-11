# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract tests for scripts/tdd_cycle.sh."""

import os
import subprocess


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(PROJECT_ROOT, "scripts", "tdd_cycle.sh")


def test_smoke_true():
    assert True


def test_script_exists_and_is_executable():
    assert os.path.exists(SCRIPT_PATH)
    assert os.access(SCRIPT_PATH, os.X_OK)


def test_help_lists_supported_commands():
    result = subprocess.run(
        [SCRIPT_PATH, "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    output = f"{result.stdout}\n{result.stderr}"
    for command in ("red", "green", "refactor", "gate"):
        assert command in output


def test_green_runs_targeted_node():
    result = subprocess.run(
        [SCRIPT_PATH, "green", "tests/test_tdd_cycle_script.py::test_smoke_true"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
