# SPDX-License-Identifier: AGPL-3.0-or-later
"""Public repository self-hosting documentation contracts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_documented_install_paths_are_backed_by_repository_files():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "curl -fsSL https://openleg.ch/install.sh | bash" in readme
    assert (ROOT / "scripts/install.sh").is_file()
    assert (ROOT / "scripts/openleg").is_file()
    assert (ROOT / "docker-compose.yml").is_file()
    assert "docker compose up -d" in readme


def test_readme_orients_english_and_german_technical_users():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "## English" in readme
    assert "## Deutsch" in readme
    assert "### Current billing boundary" in readme
    assert "### Aktuelle Abrechnungsgrenze" in readme
    assert "billing_engine.py" in readme
    assert "scripts/tdd_cycle.sh gate" in readme
