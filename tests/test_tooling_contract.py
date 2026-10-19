# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contracts for development tooling upgrades."""

import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_ruff_017_is_pinned_with_reviewed_exceptions():
    config = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    requirements = (PROJECT_ROOT / "requirements-dev.txt").read_text().splitlines()
    runtime_requirements = (PROJECT_ROOT / "requirements.txt").read_text().splitlines()

    assert "ruff==0.17.1" in requirements
    assert "tzdata==2026.5" in runtime_requirements
    assert config["tool"]["ruff"]["lint"]["ignore"] == ["BLE001", "SIM117"]
