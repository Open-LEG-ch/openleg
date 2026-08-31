# SPDX-License-Identifier: AGPL-3.0-or-later
"""The public engineering contract requires reproducible mutation verification.

Every security claim in this repository is meant to be checked by breaking the
production code and watching the suite go red. That was done by hand, one edit
at a time, and the result lived in a pull-request description. Neither coverage
nor mutmut was installed or declared, so no earlier claim about a coverage or
mutation pass on this repository could be reproduced from its own state.
"""

from pathlib import Path

import tomllib

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The modules where a surviving mutant would mean money or privacy, and where
# the suite already asserts SQL shape rather than SQL behaviour.
SCOPED_MODULES = ("billing_runner.py", "store/metering.py")


def _pyproject():
    return tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _requirements_dev():
    return (PROJECT_ROOT / "requirements-dev.txt").read_text(encoding="utf-8")


def test_the_mutation_tools_are_pinned_as_dev_dependencies():
    requirements = _requirements_dev().splitlines()

    assert "mutmut==3.7.0" in requirements, (
        "the mutation-testing skill supports exactly 3.7.0"
    )
    assert any(line.startswith("coverage==") for line in requirements)


def test_mutmut_is_scoped_to_the_modules_where_a_survivor_would_matter():
    config = _pyproject()

    assert "mutmut" in config["tool"], "the skill requires a [tool.mutmut] table"
    source_paths = config["tool"]["mutmut"]["source_paths"]
    assert list(source_paths) == list(SCOPED_MODULES)


def test_the_mutant_cache_is_not_committed():
    ignored = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "mutants/" in ignored
    assert ".mutmut-cache" in ignored


def test_the_gate_script_offers_the_mutation_pass():
    """The branch has to run mutmut, not merely mention it."""
    script = (PROJECT_ROOT / "scripts" / "tdd_cycle.sh").read_text(encoding="utf-8")

    start = script.index("  mutants)")
    branch = script[start : script.index("    ;;", start)]

    assert "-m mutmut run" in branch
    assert "-m mutmut results" in branch
    assert "pyproject" in branch, (
        "the command must run the configured scope, not an ad-hoc path list"
    )


def test_the_docs_warn_that_coverage_over_the_store_layer_is_not_evidence():
    text = (PROJECT_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")

    assert "coverage" in text.lower()
    lowered = text.lower()
    assert "not evidence" in lowered or "does not prove" in lowered
