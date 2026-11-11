# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shared gate checks real source, including function bodies."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("module", ["money_display.py", "billing_approval.py"])
def test_strict_gate_rejects_type_error(tmp_path, module):
    checkout = tmp_path / "repo"
    shutil.copytree(
        ROOT,
        checkout,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "node_modules",
            "__pycache__",
            ".mypy_cache",
            ".pytest_cache",
        ),
    )
    env = {**os.environ, "PATH": f"{Path(sys.executable).parent}:{os.environ['PATH']}"}

    def run_gate():
        return subprocess.run(
            ["bash", "scripts/strict_mypy.sh"],
            cwd=checkout,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    clean = run_gate()
    assert clean.returncode == 0, clean.stdout + clean.stderr
    with (checkout / module).open("a") as source:
        source.write('\n\ndef deliberate_error() -> int:\n    return "wrong"\n')
    broken = run_gate()
    assert broken.returncode != 0
    assert module in broken.stdout
    assert "return-value" in broken.stdout


def test_local_and_ci_share_strict_gate():
    workflow = yaml.safe_load((ROOT / ".github/workflows/lint.yml").read_text())
    commands = [step.get("run", "") for step in workflow["jobs"]["lint"]["steps"]]
    assert "bash scripts/strict_mypy.sh" in commands
    local = (
        (ROOT / "scripts/tdd_cycle.sh")
        .read_text()
        .split("  gate)", 1)[1]
        .split(";;", 1)[0]
    )
    assert 'bash "${script_dir}/strict_mypy.sh"' in local
