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


def test_gate_ignores_a_stale_ruff_binary_on_path(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "stale-ruff-ran"
    fake_ruff = fake_bin / "ruff"
    fake_ruff.write_text(f"#!/bin/sh\ntouch '{marker}'\necho 'ruff 0.15.20'\nexit 99\n")
    fake_ruff.chmod(0o755)
    fake_pytest = fake_bin / "pytest"
    fake_pytest.write_text("#!/bin/sh\nexit 0\n")
    fake_pytest.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    result = subprocess.run(
        [SCRIPT_PATH, "gate"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists()


def test_gate_uses_the_selected_python_in_ruff_install_guidance(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python"
    fake_python.write_text("#!/bin/sh\necho 'ruff 0.15.20'\n")
    fake_python.chmod(0o755)
    (fake_bin / "python3").symlink_to(fake_python)
    fake_pytest = fake_bin / "pytest"
    fake_pytest.write_text("#!/bin/sh\nexit 99\n")
    fake_pytest.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    result = subprocess.run(
        [SCRIPT_PATH, "gate"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "Ruff 0.16.1 required; found 0.15.20" in result.stderr
    assert "python -m pip install -r requirements-dev.txt" in result.stderr
