from pathlib import Path

DOC_PATH = Path("docs/repo-boundary.md")


def _doc_text() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def test_repo_boundary_doc_exists() -> None:
    assert DOC_PATH.exists(), "docs/repo-boundary.md must exist"


def test_repo_boundary_doc_includes_required_check_names() -> None:
    text = _doc_text()
    assert "ci/lint" in text
    assert "ci/test" in text
    assert "ci/security" in text


def test_repo_boundary_doc_includes_pr_only_rule() -> None:
    text = _doc_text().lower()
    assert "pr-only" in text or "pull request only" in text
    assert "no direct push" in text or "no direct pushes" in text


def test_repo_boundary_doc_keeps_private_ops_procedures_private() -> None:
    text = _doc_text().lower()
    assert "separate private repository" in text
    assert "git filter-repo" not in text
    assert "git subtree split" not in text
