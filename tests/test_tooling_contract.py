# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contracts for development tooling upgrades."""

from pathlib import Path

import tomllib

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_ruff_016_is_pinned_with_reviewed_exceptions():
    config = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    requirements = (PROJECT_ROOT / "requirements-dev.txt").read_text().splitlines()
    runtime_requirements = (PROJECT_ROOT / "requirements.txt").read_text().splitlines()

    assert "ruff==0.16.3" in requirements
    assert "tzdata==2026.3" in runtime_requirements
    assert config["tool"]["ruff"]["lint"]["ignore"] == ["BLE001", "SIM117"]
