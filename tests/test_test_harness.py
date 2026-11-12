# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract for the canonical test harness (Program 9 W0).

One command, `scripts/test.sh`, is what we run on every build, deploy, and
debug. It has layered modes and its `gate` mode mirrors the required CI checks,
so a green gate locally means a green PR.
"""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

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


def _collected(*args):
    """Collect this file in a child pytest and return the selected node ids."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
            "tests/test_test_harness.py",
            *args,
        ],
        check=False,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTEST_ADDOPTS": ""},
    )
    return result, [line for line in result.stdout.splitlines() if "::" in line]


def test_shards_partition_the_collected_tests_evenly():
    """Every collected test runs in exactly one shard, and shards stay balanced."""
    runs = [
        _collected(*args) for args in ([], *([f"--shard={i}/3"] for i in (1, 2, 3)))
    ]
    assert [result.returncode for result, _ in runs] == [pytest.ExitCode.OK] * 4
    everything, *shards = (selected for _, selected in runs)
    assert sorted(node for shard in shards for node in shard) == sorted(everything)
    assert max(map(len, shards)) - min(map(len, shards)) <= 1
    assert all(shards)


@pytest.mark.parametrize("value", ["0/3", "4/3", "1/0", "one/3", "1", "²/3", "1/3/3"])
def test_invalid_shard_is_a_usage_error(value):
    result, selected = _collected(f"--shard={value}")
    assert result.returncode == pytest.ExitCode.USAGE_ERROR
    assert "--shard" in result.stderr
    assert selected == []


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("fast", ["tests/", "-q", "-m", "not integration and not smoke"]),
        ("full", ["tests/", "-q"]),
    ],
)
def test_harness_forwards_extra_pytest_arguments(tmp_path, mode, expected):
    """Extra arguments reach pytest on its command line, not through the environment.

    Child pytest processes inherit PYTEST_ADDOPTS, so a shard passed there would
    also filter tests that start pytest themselves.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    record = tmp_path / "arguments.json"
    fake_pytest = fake_bin / "pytest"
    fake_pytest.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(record)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
    )
    fake_pytest.chmod(0o755)
    result = subprocess.run(
        [TEST_SH, mode, "--shard=3/6"],
        cwd=ROOT,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(record.read_text()) == [*expected, "--shard=3/6"]
