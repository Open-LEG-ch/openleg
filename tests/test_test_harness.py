# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract for the canonical test harness (Program 9 W0).

One command, `scripts/test.sh`, is what we run on every build, deploy, and
debug. It has layered modes and its `gate` mode mirrors the required CI checks,
so a green gate locally means a green PR.
"""

import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_SH = ROOT / "scripts" / "test.sh"


def test_harness_script_exists_and_is_executable():
    assert TEST_SH.is_file()
    assert TEST_SH.stat().st_mode & stat.S_IXUSR


def test_harness_has_strict_mode_and_layered_modes():
    content = TEST_SH.read_text(encoding="utf-8")
    assert "set -euo pipefail" in content
    for mode in ("fast", "full", "gate"):
        assert mode in content


def test_gate_mode_mirrors_required_checks():
    # gate == what a PR must pass: full pytest + ruff lint + ruff format check.
    content = TEST_SH.read_text(encoding="utf-8")
    assert "pytest" in content
    assert "ruff check" in content
    assert "ruff format --check" in content


def test_pytest_markers_registered():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for marker in ("unit", "integration", "smoke", "deploy", "contract"):
        assert marker in pyproject


def test_ci_test_job_uses_canonical_harness():
    deploy = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert "scripts/test.sh" in deploy
