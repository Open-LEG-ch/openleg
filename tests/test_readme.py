from pathlib import Path


README_PATH = Path("README.md")


def _readme_text() -> str:
    return README_PATH.read_text(encoding="utf-8")


def test_readme_exists() -> None:
    assert README_PATH.exists(), "README.md must exist"


def test_readme_has_public_sections() -> None:
    text = _readme_text()
    assert "# OpenLEG" in text
    assert "## What this repo is" in text
    assert "## Quick start" in text
    assert "## Contributing" in text
    assert "## Repository boundary" in text
    assert "## Security" in text


def test_readme_has_no_private_identity_or_local_paths() -> None:
    text = _readme_text().lower()
    _forbidden = ("w" + "gusta", "baden" + "leg", "/" + "users/")
    for fragment in _forbidden:
        assert fragment not in text
