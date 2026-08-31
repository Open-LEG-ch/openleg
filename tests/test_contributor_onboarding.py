# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contracts for the public contributor onboarding guide."""

import posixpath
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
ONBOARDING = Path("docs/contributor-onboarding.md")

pytestmark = pytest.mark.contract


def test_onboarding_guide_exists_and_is_tracked() -> None:
    assert (ROOT / ONBOARDING).is_file()
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ONBOARDING.as_posix()],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked.returncode == 0, tracked.stderr


def test_onboarding_guide_is_linked_from_public_entry_points() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    english, german = readme.split("## Deutsch", maxsplit=1)
    link = re.compile(r"\[[^]]+\]\(docs/contributor-onboarding\.md\)")

    assert link.search(english), "English README needs a complete markdown link"
    assert link.search(german), "German README needs a complete markdown link"
    assert link.search((ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")), (
        "CONTRIBUTING.md needs a complete markdown link"
    )


def test_onboarding_guide_links_only_tracked_public_files() -> None:
    guide = (ROOT / ONBOARDING).read_text(encoding="utf-8")
    forbidden = (
        "AGENTS.md",
        "CLAUDE.md",
        "docs/openleg-open-source-standard.md",
        "prd/",
    )
    assert not any(path in guide for path in forbidden)

    # Every relative link target, not only .md: a link to a missing script or
    # directory is just as broken, and only external URLs are out of scope.
    targets = [
        target
        for target in re.findall(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)", guide)
        if not re.match(r"^[a-z][a-z0-9+.-]*://", target)
        and not target.startswith("mailto:")
    ]
    resolved = {
        posixpath.normpath(posixpath.join(ONBOARDING.parent.as_posix(), target))
        for target in targets
    }
    tracked = set(
        subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    )

    assert resolved == {
        "CONTRIBUTING.md",
        "docs/codex-execution.md",
        "docs/repo-boundary.md",
    }
    assert resolved <= tracked
