# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guard against dangling Markdown references in public docs.

Stale pointers cost every reader (and every agent) a wasted lookup. Any
`docs/foo.md`-style path a public doc names must resolve to a tracked file.
"""

import os
import pathlib
import re
import subprocess

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Forbidden normalized path components.
_FORBIDDEN = {"prd", "internal", "strategy", "grants", "private"}

# Backtick paths and Markdown link targets that end in .md.
# Group 1: backtick path; Group 2: markdown link target (raw, may include brackets, fragment, title).
_REFERENCE = re.compile(
    r"`([A-Za-z0-9_./-]+\.md)`"
    r"|"
    r"\]\(((?:<[^>\n]+?\.md(?:#[^>\n]*)?>|[^\s<>)\n]+?\.md(?:#[^\s)\n]*)?)"
    r"(?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^\)\n]*\)))?)\)"
)


def _normalize_markdown_target(raw_target):
    """Normalize a raw Markdown link destination.

    * Strips optional title: double-quoted, single-quoted, or parenthesized.
    * Strips optional surrounding angle brackets: <...>
    * Strips URL fragment.
    * Returns the cleaned path, or None if it is not a .md file target.
    """
    target = raw_target.strip()

    # Strip optional title: double-quoted, single-quoted, or parenthesized.
    # Try parenthesized first so we don't split inside quotes.
    for pattern in (r"\s+\([^)]*\)$", r'\s+"[^"]*"$', r"\s+'[^']*'$"):
        m = re.search(pattern, target)
        if m:
            target = target[: m.start()].strip()
            break

    # Strip optional surrounding angle brackets.
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()

    # Strip fragment.
    target = target.split("#")[0].strip()

    if not target.endswith(".md"):
        return None
    return target


def _is_url_or_non_file_target(target):
    """Return True for HTTP(S) URLs, anchors, mailto links, and non-.md targets."""
    # Normalize first so angle-bracket URLs are stripped before scheme check.
    stripped = _normalize_markdown_target(target)
    return stripped is None or stripped.startswith(
        ("http://", "https://", "mailto:", "#")
    )


def _resolve_target(raw_target, source_doc, root):
    """Canonicalize a raw .md reference.

    * Backtick paths (source_doc is None) are repo-root-relative.
    * Markdown link paths are relative to the source document's directory.
    * URL fragments and optional quoted/parenthesized titles are stripped before resolution.
    * Returns the repo-root-relative forward-slash path, or None if the target
      escapes the repository root or traverses into a forbidden directory.
    """
    target = raw_target.strip()

    target = _normalize_markdown_target(target)
    if target is None:
        return None

    if _is_url_or_non_file_target(target):
        return None

    if source_doc is None:
        # Backtick path: repo-root-relative.
        resolved = target
    else:
        # Markdown link: relative to source document's directory.
        source_dir = os.path.dirname(source_doc)
        resolved = os.path.normpath(os.path.join(source_dir, target))

    # Reject absolute paths.
    if os.path.isabs(resolved):
        return None

    # Canonicalize against root and reject targets that escape it.
    root_path = pathlib.Path(root).resolve()
    abs_resolved = (root_path / resolved).resolve()
    try:
        rel = abs_resolved.relative_to(root_path)
    except ValueError:
        return None

    # Reject forbidden normalized path components.
    parts = set(str(rel).replace(os.sep, "/").split("/"))
    if parts & _FORBIDDEN:
        return None

    return str(rel).replace(os.sep, "/")


def _public_docs():
    docs = [
        "README.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CLAUDE.md",
        "AGENTS.md",
        "CONTEXT.md",
    ]
    docs_dir = os.path.join(PROJECT_ROOT, "docs")
    if os.path.isdir(docs_dir):
        for name in sorted(os.listdir(docs_dir)):
            if name.endswith(".md"):
                docs.append(os.path.join("docs", name))
    return [d for d in docs if os.path.exists(os.path.join(PROJECT_ROOT, d))]


def _tracked_files(root=PROJECT_ROOT):
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return frozenset(result.stdout.decode().split("\0"))


TRACKED_FILES = _tracked_files()


def test_public_docs_handles_a_checkout_without_a_docs_directory(tmp_path, monkeypatch):
    (tmp_path / "README.md").write_text("# Test", encoding="utf-8")
    monkeypatch.setitem(globals(), "PROJECT_ROOT", str(tmp_path))

    assert _public_docs() == ["README.md"]


def _references(relative_path, root=PROJECT_ROOT):
    with open(os.path.join(root, relative_path), encoding="utf-8") as handle:
        text = handle.read()
    results = []
    for backtick, link in _REFERENCE.findall(text):
        raw = backtick or link
        if backtick:
            results.append((backtick, relative_path, True))
        elif raw and not _is_url_or_non_file_target(raw):
            results.append((raw, relative_path, False))
    return results


def _unsafe_references(relative_path, root=PROJECT_ROOT):
    """Return references that resolve to unsafe paths (escape root or hit forbidden)."""
    unsafe = []
    for raw_target, source_doc, is_backtick in _references(relative_path, root=root):
        target = _resolve_target(raw_target, None if is_backtick else source_doc, root)
        if target is None:
            unsafe.append(raw_target)
    return unsafe


@pytest.mark.parametrize("doc", _public_docs())
def test_referenced_markdown_files_exist_and_are_tracked(doc):
    missing = []
    for raw_target, source_doc, is_backtick in _references(doc):
        target = _resolve_target(
            raw_target, None if is_backtick else source_doc, PROJECT_ROOT
        )
        if target is None:
            continue
        if (
            not os.path.exists(os.path.join(PROJECT_ROOT, target))
            or target not in TRACKED_FILES
        ):
            missing.append(raw_target)
    assert missing == [], f"{doc} points at missing files: {sorted(set(missing))}"


@pytest.mark.parametrize("doc", _public_docs())
def test_public_docs_do_not_point_into_unsafe_paths(doc):
    unsafe = _unsafe_references(doc, root=PROJECT_ROOT)
    assert unsafe == [], f"{doc} points into an unsafe path: {unsafe}"


# --- Unit/contract tests for the validator itself --------------------------------


def test_backtick_path_is_repo_root_relative(tmp_path):
    assert _resolve_target("docs/guide.md", None, str(tmp_path)) == "docs/guide.md"


def test_markdown_link_is_source_document_relative(tmp_path):
    assert (
        _resolve_target("guide.md", "docs/api/index.md", str(tmp_path))
        == "docs/api/guide.md"
    )


def test_markdown_link_parent_relative_inside_root(tmp_path):
    assert (
        _resolve_target("../guide.md", "docs/api/index.md", str(tmp_path))
        == "docs/guide.md"
    )


def test_markdown_link_fragment_is_stripped(tmp_path):
    assert (
        _resolve_target("guide.md#section", "docs/api/index.md", str(tmp_path))
        == "docs/api/guide.md"
    )


def test_markdown_link_quoted_title_is_stripped(tmp_path):
    assert (
        _resolve_target('guide.md "title"', "docs/api/index.md", str(tmp_path))
        == "docs/api/guide.md"
    )


def test_markdown_link_fragment_and_title_are_stripped(tmp_path):
    assert (
        _resolve_target('guide.md#section "title"', "docs/api/index.md", str(tmp_path))
        == "docs/api/guide.md"
    )


def test_path_outside_repo_root_is_rejected(tmp_path):
    assert (
        _resolve_target("../../../README.md", "docs/api/index.md", str(tmp_path))
        is None
    )


def test_traversal_after_normalization_is_rejected(tmp_path):
    assert (
        _resolve_target("../private/secret.md", "docs/api/index.md", str(tmp_path))
        is None
    )


def test_private_path_after_normalization_docs_dot_dot_private(tmp_path):
    """docs/../private/secret.md must be rejected after normalization."""
    assert (
        _resolve_target("docs/../private/secret.md", "README.md", str(tmp_path)) is None
    )


def test_url_target_is_ignored():
    assert _is_url_or_non_file_target("https://example.com") is True
    assert _is_url_or_non_file_target("http://example.com/file.md") is True


def test_anchor_target_is_ignored():
    assert _is_url_or_non_file_target("#section") is True


def test_mailto_target_is_ignored():
    assert _is_url_or_non_file_target("mailto:foo@example.com") is True


def test_non_md_target_is_ignored():
    assert _is_url_or_non_file_target("https://example.com/file.pdf") is True
    assert _is_url_or_non_file_target("guide.pdf") is True
    assert _is_url_or_non_file_target("guide.md") is False


def test_markdown_link_angle_bracket_destination_with_fragment(tmp_path):
    assert (
        _resolve_target("<guide.md#part>", "docs/api/index.md", str(tmp_path))
        == "docs/api/guide.md"
    )


def test_markdown_link_single_quoted_title_is_stripped(tmp_path):
    assert (
        _resolve_target("guide.md 'Title'", "docs/api/index.md", str(tmp_path))
        == "docs/api/guide.md"
    )


def test_markdown_link_parenthesized_title_is_stripped(tmp_path):
    assert (
        _resolve_target("guide.md (Title)", "docs/api/index.md", str(tmp_path))
        == "docs/api/guide.md"
    )


def test_angle_bracket_forbidden_destination_resolves_unsafe(tmp_path):
    assert (
        _resolve_target("<../private/secret.md>", "docs/api/index.md", str(tmp_path))
        is None
    )


def test_references_distinguishes_backtick_and_link(tmp_path):
    doc = tmp_path / "docs" / "api.md"
    doc.parent.mkdir(parents=True)
    (tmp_path / "other.md").write_text("# other", encoding="utf-8")
    (tmp_path / "docs" / "guide.md").write_text("# guide", encoding="utf-8")
    doc.write_text(
        'See `other.md` and [guide](guide.md#intro "Guide").', encoding="utf-8"
    )
    refs = _references("docs/api.md", root=str(tmp_path))
    refs = sorted(refs, key=lambda r: r[0])
    assert len(refs) == 2
    assert refs[0] == ('guide.md#intro "Guide"', "docs/api.md", False)
    assert refs[1] == ("other.md", "docs/api.md", True)


def test_parser_recognizes_angle_single_paren_double_title_forms(tmp_path):
    doc = tmp_path / "docs" / "api.md"
    doc.parent.mkdir(parents=True)
    (tmp_path / "docs" / "guide.md").write_text("# guide", encoding="utf-8")
    doc.write_text(
        "[a](<guide.md#part>)\n"
        "[b](guide.md 'Single')\n"
        "[c](guide.md (Paren))\n"
        '[d](guide.md "Double")\n'
        '[e](<guide.md#part> "Angle")\n',
        encoding="utf-8",
    )
    refs = _references("docs/api.md", root=str(tmp_path))
    assert len(refs) == 5
    assert all(not is_backtick for _, _, is_backtick in refs)
    raw_targets = [r[0] for r in refs]
    angle_raw = '<guide.md#part> "Angle"'
    assert angle_raw in raw_targets
    assert _resolve_target(angle_raw, "docs/api.md", str(tmp_path)) == "docs/guide.md"
    assert "<guide.md#part>" in raw_targets
    assert "guide.md 'Single'" in raw_targets
    assert "guide.md (Paren)" in raw_targets
    assert 'guide.md "Double"' in raw_targets


def test_parser_ignores_angle_bracket_remote_url(tmp_path):
    doc = tmp_path / "docs" / "api.md"
    doc.parent.mkdir(parents=True)
    (tmp_path / "docs" / "guide.md").write_text("# guide", encoding="utf-8")
    doc.write_text(
        "[x](<https://example.com/remote.md>)\n[y](guide.md)\n",
        encoding="utf-8",
    )
    refs = _references("docs/api.md", root=str(tmp_path))
    assert len(refs) == 1
    assert refs[0] == ("guide.md", "docs/api.md", False)


def test_parser_boundary_unsafe_paths_via_helper(tmp_path):
    doc = tmp_path / "docs" / "api.md"
    doc.parent.mkdir(parents=True)
    (tmp_path / "docs" / "guide.md").write_text("# guide", encoding="utf-8")
    doc.write_text(
        "[x](<../private/secret.md>)\n"
        "[y](../private/other.md 'Title')\n"
        "[z](guide.md)\n",
        encoding="utf-8",
    )
    unsafe = _unsafe_references("docs/api.md", root=str(tmp_path))
    assert "<../private/secret.md>" in unsafe
    assert "../private/other.md 'Title'" in unsafe
    assert len(unsafe) == 2


def test_references_ignores_urls_and_non_md(tmp_path):
    doc = tmp_path / "docs" / "api.md"
    doc.parent.mkdir(parents=True)
    (tmp_path / "local.md").write_text("# local", encoding="utf-8")
    doc.write_text(
        "See [a](local.md), [b](https://example.com/remote.md), "
        "[c](mailto:x@y.com), [d](#anchor), [e](page.pdf).",
        encoding="utf-8",
    )
    refs = _references("docs/api.md", root=str(tmp_path))
    assert len(refs) == 1
    assert refs[0] == ("local.md", "docs/api.md", False)
