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

# The modules where a surviving mutant would mean money, privacy, or a wrong
# formation decision, and where the suite already asserts SQL shape rather than
# SQL behaviour. The scope follows the slice under evidence; the formation
# seam refactor (#453) moved readiness, transitions, and the consent-gated
# cluster decision into formation_wizard and store/formation. Gemeindeprofil
# source outcomes and preservation policy are owned by public_data (#455).
# Address normalization and fallback outcomes are owned by data_enricher (#456).
# neighbor_view.py owns privacy-critical coordinate disclosure decisions.
SCOPED_MODULES = (
    "billing_policy.py",
    "billing_approval.py",
    "billing_runner.py",
    "store/metering.py",
    "formation_wizard.py",
    "store/formation.py",
    "public_data.py",
    "data_enricher.py",
    "member_invoices.py",
    "neighbor_view.py",
)


def _pyproject():
    return tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _requirements_dev():
    return (PROJECT_ROOT / "requirements-dev.txt").read_text(encoding="utf-8")


def _mutmut_config():
    config = _pyproject()
    assert "tool" in config, "pyproject.toml requires a [tool] table"
    assert "mutmut" in config["tool"], "the skill requires a [tool.mutmut] table"
    return config["tool"]["mutmut"]


def test_the_mutation_tools_are_pinned_as_dev_dependencies():
    requirements = _requirements_dev().splitlines()

    assert "mutmut==3.7.0" in requirements, (
        "the mutation-testing skill supports exactly 3.7.0"
    )
    assert any(line.startswith("coverage==") for line in requirements)


def test_mutmut_is_scoped_to_the_modules_where_a_survivor_would_matter():
    config = _mutmut_config()

    assert "source_paths" in config, "mutmut requires an explicit source scope"
    source_paths = config["source_paths"]
    assert list(source_paths) == list(SCOPED_MODULES)


def test_mutmut_copies_formation_dependencies_needed_for_collection():
    config = _mutmut_config()

    assert "also_copy" in config, "mutmut requires explicit sandbox dependencies"
    also_copy = set(config["also_copy"])

    assert {
        "access_token.py",
        "billing_approval.py",
        "billing_lifecycle.py",
        "billing_policy.py",
        "email_automation.py",
        "email_utils.py",
    } <= also_copy


def _repo_module_imports(module_file):
    """Repo paths the file reaches through imports, transitively.

    Imports inside function bodies count too: mutmut runs the scoring tests
    inside its sandbox, so a lazy import of an uncopied module breaks scoring
    mid-suite, not just at collection time. Dotted names resolve to a sibling
    module or into a package's modules.
    """
    import ast

    pending = [module_file]
    seen = set()
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.add(node.module)
        for name in names:
            candidate = PROJECT_ROOT / (name.replace(".", "/") + ".py")
            if candidate.is_file():
                pending.append(candidate)
                continue
            package_init = PROJECT_ROOT / name.replace(".", "/") / "__init__.py"
            if package_init.is_file():
                pending.append(package_init)
                pending.extend(sorted(package_init.parent.glob("*.py")))
    return {path.relative_to(PROJECT_ROOT).as_posix() for path in seen}


def test_mutmut_copies_every_repo_module_the_app_imports():
    """The scoring tests import app.py, and mutmut runs them from the sandbox.

    A repository module that app.py imports but also_copy omits makes every
    test session inside the sandbox die on ModuleNotFoundError before any
    mutant is scored. #372 hit this with access_token; the clustering_run
    import in #453 hit it again. The contract walks the transitive import
    closure of app.py so it tracks the code instead of a copied list.
    """
    config = _mutmut_config()
    copied = set(config["also_copy"]) | set(config["source_paths"])
    copied_dirs = {
        f"{entry.rstrip('/')}/" for entry in copied if (PROJECT_ROOT / entry).is_dir()
    }

    missing = _repo_module_imports(PROJECT_ROOT / "app.py") - copied
    missing = {path for path in missing if not path.startswith(tuple(copied_dirs))}

    assert not missing, (
        f"app.py reaches {sorted(missing)} and mutmut does not copy them "
        "into the sandbox; add them to also_copy or scoring fails"
    )


def test_the_test_file_list_is_selection_args_not_mutant_child_args():
    """mutmut appends pytest_add_cli_args to every mutant child's pytest run.

    The list of test files decides which tests score the mutants; it is not a
    command to execute inside each child against mutated code. Those paths
    belong in pytest_add_cli_args_test_selection, which limits stats
    collection. A test path left in pytest_add_cli_args would also be appended
    to every mutant child.
    """
    config = _mutmut_config()

    selection = config.get("pytest_add_cli_args_test_selection")
    assert selection, (
        "the test file list must live in pytest_add_cli_args_test_selection"
    )
    assert all(
        str(path).startswith("tests/") and str(path).endswith(".py")
        for path in selection
    ), "pytest_add_cli_args_test_selection selects the scoring test files"

    appended = config.get("pytest_add_cli_args", ())
    stray = [str(arg) for arg in appended if str(arg).startswith("tests/")]
    assert not stray, (
        "pytest_add_cli_args reaches every mutant child; test file paths "
        f"belong in pytest_add_cli_args_test_selection instead: {stray}"
    )


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
