# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract tests for the public domain vocabulary in CONTEXT.md."""

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOC_PATH = PROJECT_ROOT / "CONTEXT.md"

SHIPPED_STORES = (
    "store/ranking",
    "store/profile",
    "store/billing",
    "store/email_queue",
    "store/utility",
    "store/metering",
)


def _doc_text() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def test_context_doc_exists() -> None:
    assert DOC_PATH.exists(), "CONTEXT.md must exist"


def test_context_doc_is_tracked_not_gitignored() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", "CONTEXT.md"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
    )
    assert tracked.returncode == 0, "CONTEXT.md must be tracked"

    ignored = subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", "CONTEXT.md"],
        cwd=PROJECT_ROOT,
        check=False,
    )
    assert ignored.returncode != 0, "CONTEXT.md must be allowlisted in .gitignore"


def test_context_doc_names_the_connection_seam() -> None:
    text = _doc_text()
    assert "get_connection" in text
    assert "database.py" in text


def test_context_doc_lists_every_shipped_store_module() -> None:
    text = _doc_text()
    missing = [name for name in SHIPPED_STORES if name not in text]
    assert missing == [], f"CONTEXT.md is missing store modules: {missing}"


def test_context_doc_covers_core_domain_vocabulary() -> None:
    text = _doc_text()
    for term in ("LEG", "VNB", "SDAT", "ElCom", "BFS", "Messpunkt"):
        assert term in text, f"CONTEXT.md must define the term {term}"


def test_context_doc_stays_public_safe() -> None:
    lowered = _doc_text().lower()
    for leak in ("password", "api_key=", "secret=", "ssh ", "ansible"):
        assert leak not in lowered, f"CONTEXT.md must not carry {leak}"


def test_context_doc_uses_swiss_german_orthography() -> None:
    assert "ß" not in _doc_text(), "Use ss instead of ß"


def test_shipped_store_modules_actually_exist() -> None:
    for name in SHIPPED_STORES:
        module = PROJECT_ROOT / f"{name}.py"
        assert module.exists() or (PROJECT_ROOT / name).is_dir(), f"{name} is missing"


def test_engineering_contract_points_to_public_context() -> None:
    contract = (PROJECT_ROOT / "docs" / "engineering-contract.md").read_text(
        encoding="utf-8"
    )
    assert "CONTEXT.md" in contract
    assert "prd/" not in contract


def test_project_root_is_a_repository() -> None:
    assert (PROJECT_ROOT / ".git").exists()
