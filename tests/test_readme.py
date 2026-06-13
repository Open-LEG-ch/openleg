from pathlib import Path


README_PATH = Path("README.md")
ARCHITECTURE_DOC = Path("docs/architecture.md")
DATA_PIPELINE_DOC = Path("docs/data-pipeline.md")
API_EXAMPLES_DOC = Path("docs/api-examples.md")


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


def test_readme_links_to_public_architecture_docs() -> None:
    text = _readme_text()
    assert "docs/architecture.md" in text
    assert "docs/data-pipeline.md" in text
    assert "docs/api-examples.md" in text
    assert "Route map" in text
    assert "Data pipeline" in text


def test_public_architecture_docs_exist_and_explain_system() -> None:
    assert ARCHITECTURE_DOC.exists()
    assert DATA_PIPELINE_DOC.exists()
    assert API_EXAMPLES_DOC.exists()

    architecture = ARCHITECTURE_DOC.read_text(encoding="utf-8")
    assert "Flask" in architecture
    assert "PostgreSQL" in architecture
    assert "Redis" in architecture
    assert "Caddy" in architecture
    assert "Route map" in architecture

    pipeline = DATA_PIPELINE_DOC.read_text(encoding="utf-8")
    assert "BFE Anlagenregister" in pipeline
    assert "BFE Sonnendach" in pipeline
    assert "BFS" in pipeline
    assert "load_pv_data.py" in pipeline

    api_examples = API_EXAMPLES_DOC.read_text(encoding="utf-8")
    assert "curl" in api_examples
    assert "/api/v1/municipalities" in api_examples
    assert "/api/v1/search" in api_examples


def test_readme_has_no_private_identity_or_local_paths() -> None:
    text = _readme_text().lower()
    _forbidden = ("w" + "gusta", "baden" + "leg", "/" + "users/")
    for fragment in _forbidden:
        assert fragment not in text
