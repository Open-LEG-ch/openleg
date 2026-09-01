# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract tests for the contributor workbench CLI."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRIBUTE = PROJECT_ROOT / "scripts" / "contribute"

pytestmark = pytest.mark.contract

_MATCH_MODULE_RUFF = object()


def _fake_python(
    tmp_path,
    version,
    *,
    pytest_present=True,
    ruff_version=None,
    bare_ruff_version=_MATCH_MODULE_RUFF,
    commands=(),
    isolated_path=False,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python"
    ruff_result = f"echo 'ruff {ruff_version}'; exit 0" if ruff_version else "exit 1"
    fake_python.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = "--version" ]; then echo \'Python {version}\'; exit 0; fi\n'
        f'if [ "$1" = "-c" ] && [ "$2" = "import pytest" ]; then exit {0 if pytest_present else 1}; fi\n'
        f'if [ "$1" = "-m" ] && [ "$2" = "ruff" ] && [ "$3" = "--version" ]; then {ruff_result}; fi\n'
        f"exec '{sys.executable}' \"$@\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    (fake_bin / "python3").symlink_to(fake_python)
    shadow_missing_ruff = (
        bare_ruff_version is _MATCH_MODULE_RUFF and ruff_version is None
    )
    if bare_ruff_version is _MATCH_MODULE_RUFF:
        bare_ruff_version = ruff_version
    if shadow_missing_ruff:
        bare_ruff = fake_bin / "ruff"
        bare_ruff.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        bare_ruff.chmod(0o755)
    elif bare_ruff_version is not None:
        bare_ruff = fake_bin / "ruff"
        bare_ruff.write_text(
            f"#!/bin/sh\necho 'ruff {bare_ruff_version}'\n", encoding="utf-8"
        )
        bare_ruff.chmod(0o755)
    for command in commands:
        executable = fake_bin / command
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
    if isolated_path:
        (fake_bin / "bash").symlink_to("/bin/bash")
        (fake_bin / "dirname").symlink_to("/usr/bin/dirname")
        path = str(fake_bin)
    else:
        path = f"{fake_bin}:{os.environ['PATH']}"
    return os.environ | {"PATH": path}


def _ruff_pin(root=PROJECT_ROOT):
    return next(
        line.removeprefix("ruff==")
        for line in (root / "requirements-dev.txt").read_text().splitlines()
        if line.startswith("ruff==")
    )


def _domain_terms(root=PROJECT_ROOT):
    context = (root / "CONTEXT.md").read_text(encoding="utf-8")
    section = context.split("## Domain Terms", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("|")]
    return {
        cells[0]: cells[1]
        for line in rows[2:]
        if len(cells := [cell.strip() for cell in line.strip("|").split("|")]) == 2
    }


def _store_modules(root=PROJECT_ROOT):
    context = (root / "CONTEXT.md").read_text(encoding="utf-8")
    section = context.split("## Module Names", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("|")]
    return [
        cells[0].strip("`")
        for line in rows[2:]
        if len(cells := [cell.strip() for cell in line.strip("|").split("|")]) == 2
        and cells[0].startswith("`store/")
    ]


def test_help_exits_zero():
    result = subprocess.run(
        [CONTRIBUTE, "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_missing_python_exits_nonzero_and_names_python(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "bash").symlink_to("/bin/bash")
    (fake_bin / "dirname").symlink_to("/usr/bin/dirname")

    result = subprocess.run(
        [CONTRIBUTE, "doctor"],
        cwd=PROJECT_ROOT,
        env=os.environ | {"PATH": str(fake_bin)},
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Python" in output
    assert "install Python 3.11 or newer" in output


def test_python_3_10_exits_nonzero_and_names_minimum(tmp_path):
    result = subprocess.run(
        [CONTRIBUTE, "doctor"],
        cwd=PROJECT_ROOT,
        env=_fake_python(tmp_path, "3.10.14"),
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Python" in output
    assert "3.11" in output


def test_missing_pytest_exits_nonzero_with_install_command(tmp_path):
    result = subprocess.run(
        [CONTRIBUTE, "doctor"],
        cwd=PROJECT_ROOT,
        env=_fake_python(tmp_path, "3.12.0", pytest_present=False),
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "pytest" in output
    assert "python3 -m pip install -r requirements-dev.txt" in output


def test_missing_ruff_exits_nonzero_and_names_ruff(tmp_path):
    result = subprocess.run(
        [CONTRIBUTE, "doctor"],
        cwd=PROJECT_ROOT,
        env=_fake_python(tmp_path, "3.12.0"),
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "ruff" in output
    assert "python3 -m pip install -r requirements-dev.txt" in output


def test_wrong_ruff_version_reports_required_and_found_versions(tmp_path):
    required = _ruff_pin()
    found = "0.15.20"
    result = subprocess.run(
        [CONTRIBUTE, "doctor"],
        cwd=PROJECT_ROOT,
        env=_fake_python(tmp_path, "3.12.0", ruff_version=found),
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert required in output
    assert found in output


def test_isolated_checkout_reports_its_ruff_pin(tmp_path):
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(CONTRIBUTE, scripts / "contribute")
    shutil.copy(
        PROJECT_ROOT / "scripts" / "contributor_workbench.py",
        scripts / "contributor_workbench.py",
    )
    changed_pin = "9.8.7"
    (checkout / "requirements-dev.txt").write_text(
        f"-r requirements.txt\nruff=={changed_pin}\n", encoding="utf-8"
    )

    result = subprocess.run(
        [scripts / "contribute", "doctor"],
        cwd=checkout,
        env=_fake_python(tmp_path, "3.12.0", ruff_version="0.15.20"),
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert changed_pin in output


def test_healthy_environment_exits_zero_and_prints_group_headings(tmp_path):
    result = subprocess.run(
        [CONTRIBUTE, "doctor"],
        cwd=PROJECT_ROOT,
        env=_fake_python(
            tmp_path,
            "3.12.0",
            ruff_version=_ruff_pin(),
            commands=("node", "npm", "mypy"),
        ),
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Required to run the gate" in output
    assert "Required to match CI" in output
    assert "Reported only" in output


@pytest.mark.parametrize("missing", ["node", "npm", "mypy"])
def test_missing_ci_tool_warns_and_exits_zero(tmp_path, missing):
    present = tuple({"node", "npm", "mypy"} - {missing})
    result = subprocess.run(
        [CONTRIBUTE, "doctor"],
        cwd=PROJECT_ROOT,
        env=_fake_python(
            tmp_path,
            "3.12.0",
            ruff_version=_ruff_pin(),
            commands=present,
            isolated_path=True,
        ),
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert f"WARNING {missing}: not found" in output
    assert f"CI runs {missing}; install it to match CI locally" in output


def test_failing_docker_does_not_change_exit_code(tmp_path):
    env = _fake_python(
        tmp_path,
        "3.12.0",
        ruff_version=_ruff_pin(),
        commands=("node", "npm", "mypy"),
    )
    fake_bin = Path(env["PATH"].split(os.pathsep)[0])
    docker = fake_bin / "docker"
    docker.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    docker.chmod(0o755)

    result = subprocess.run(
        [CONTRIBUTE, "doctor"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_doctor_reports_old_interpreter_instead_of_crashing(tmp_path):
    """The workbench must run on the interpreter it is diagnosing.

    macOS ships Python 3.9 at /usr/bin/python3. A contributor there must get
    the version verdict, not a traceback from parsing the workbench itself.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    old_python = fake_bin / "python3"
    old_python.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "Python 3.9.6"; exit 0; fi\n'
        'if [ "$1" = "-c" ]; then echo "3.9"; exit 0; fi\n'
        'exec /usr/bin/python3 "$@"\n',
        encoding="utf-8",
    )
    old_python.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [CONTRIBUTE, "doctor"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "Traceback" not in output, output
    assert "3.11" in output, output


def test_workbench_annotations_are_safe_on_python_3_9():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--isolated",
            "--target-version",
            "py39",
            "--select",
            "FA102",
            PROJECT_ROOT / "scripts" / "contributor_workbench.py",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_doctor_reports_a_missing_ruff_pin_instead_of_crashing(tmp_path):
    """doctor runs on broken machines, so no path may end in a traceback.

    A requirements-dev.txt with no exact ruff pin is a repository problem the
    contributor needs told, not a stack trace they have to decode.
    """
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(CONTRIBUTE, scripts / "contribute")
    shutil.copy(
        PROJECT_ROOT / "scripts" / "contributor_workbench.py",
        scripts / "contributor_workbench.py",
    )
    (checkout / "requirements-dev.txt").write_text(
        "-r requirements.txt\npytest>=9.1.1\n", encoding="utf-8"
    )

    result = subprocess.run(
        [scripts / "contribute", "doctor"],
        cwd=checkout,
        env=_fake_python(tmp_path, "3.12.0", ruff_version="0.16.5"),
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "Traceback" not in output, output
    assert "requirements-dev.txt" in output, output


def test_glossary_term_prints_context_meaning():
    result = subprocess.run(
        [CONTRIBUTE, "glossary", "LEG"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == _domain_terms()["LEG"]


def test_bare_glossary_lists_every_context_term():
    result = subprocess.run(
        [CONTRIBUTE, "glossary"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    terms = _domain_terms()
    output_lines = result.stdout.splitlines()
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(output_lines) == len(terms)
    assert output_lines == [f"{term}: {meaning}" for term, meaning in terms.items()]


def test_glossary_term_matching_is_case_insensitive():
    uppercase = subprocess.run(
        [CONTRIBUTE, "glossary", "LEG"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    lowercase = subprocess.run(
        [CONTRIBUTE, "glossary", "leg"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert lowercase.returncode == 0, lowercase.stdout + lowercase.stderr
    assert lowercase.stdout == uppercase.stdout


def test_unknown_glossary_term_suggests_a_context_term():
    result = subprocess.run(
        [CONTRIBUTE, "glossary", "ZZZNOTATERM"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert any(term in output for term in _domain_terms())


def test_tour_names_both_context_seams():
    result = subprocess.run(
        [CONTRIBUTE, "tour"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "database.get_connection" in output
    assert "tenant.get_tenant_config" in output


def test_tour_lists_every_context_store_module():
    result = subprocess.run(
        [CONTRIBUTE, "tour"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert all(module in output for module in _store_modules())


def test_missing_context_sections_are_reported_without_tracebacks(tmp_path):
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(CONTRIBUTE, scripts / "contribute")
    shutil.copy(
        PROJECT_ROOT / "scripts" / "contributor_workbench.py",
        scripts / "contributor_workbench.py",
    )
    (checkout / "CONTEXT.md").write_text(
        "# Context\n\nThis checkout has no glossary or seam definitions.\n",
        encoding="utf-8",
    )

    for command in ("glossary", "tour"):
        result = subprocess.run(
            [scripts / "contribute", command],
            cwd=checkout,
            capture_output=True,
            text=True,
            check=False,
        )

        output = result.stdout + result.stderr
        assert result.returncode != 0, output
        assert "CONTEXT.md" in output
        assert "Traceback" not in output


def _context_checkout(tmp_path, context_body):
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(CONTRIBUTE, scripts / "contribute")
    shutil.copy(
        PROJECT_ROOT / "scripts" / "contributor_workbench.py",
        scripts / "contributor_workbench.py",
    )
    (checkout / "CONTEXT.md").write_text(context_body, encoding="utf-8")
    return checkout


def test_malformed_seam_entry_is_rejected_not_printed_blank(tmp_path):
    """A broken seam line must fail loudly, not render as an empty bullet.

    Parsing CONTEXT.md at runtime is only worth doing if the output is wrong
    loudly or right. A blank seam is wrong quietly.
    """
    checkout = _context_checkout(
        tmp_path,
        "# Context\n\n## Seams\n\n- **`\n\n## Module Names\n\n"
        "| Module | Owns |\n| --- | --- |\n| `store/x` | Something |\n",
    )

    result = subprocess.run(
        [checkout / "scripts" / "contribute", "tour"],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "Traceback" not in output, output
    assert "CONTEXT.md" in output, output


def test_table_row_with_extra_columns_is_rejected(tmp_path):
    """A three column row must fail, not smuggle the third cell into the meaning."""
    checkout = _context_checkout(
        tmp_path,
        "# Context\n\n## Domain Terms\n\n"
        "| Term | Meaning |\n| --- | --- |\n| LEG | a thing | stray |\n",
    )

    result = subprocess.run(
        [checkout / "scripts" / "contribute", "glossary", "LEG"],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "Traceback" not in output, output
    assert "stray" not in output, output


def _command_checkout(tmp_path, *, test_exit=0, cycle_exit=0):
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(CONTRIBUTE, scripts / "contribute")
    shutil.copy(
        PROJECT_ROOT / "scripts" / "contributor_workbench.py",
        scripts / "contributor_workbench.py",
    )
    for name, exit_code in (("test.sh", test_exit), ("tdd_cycle.sh", cycle_exit)):
        stub = scripts / name
        stub.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$@\" > '{checkout / f'{name}.args'}'\n"
            f"exit {exit_code}\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
    return checkout


def _setup_checkout(tmp_path):
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(CONTRIBUTE, scripts / "contribute")
    shutil.copy(
        PROJECT_ROOT / "scripts" / "contributor_workbench.py",
        scripts / "contributor_workbench.py",
    )
    (checkout / "requirements-dev.txt").write_text(
        "pytest==9.1.1\nruff==0.16.5\n", encoding="utf-8"
    )
    return checkout


def _setup_env(tmp_path, checkout, *, install_exit=0):
    fake_bin = tmp_path / "setup-bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    command_log = checkout / "python3.args"
    fake_python.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = "-c" ]; then exec \'{sys.executable}\' "$@"; fi\n'
        'if [ "$1" = "scripts/contributor_workbench.py" ]; '
        f"then exec '{sys.executable}' \"$@\"; fi\n"
        f"printf '%s\\n' \"$*\" >> '{command_log}'\n"
        'if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then\n'
        '  mkdir -p "$3/bin"\n'
        '  ln -s "$0" "$3/bin/python"\n'
        "  exit 0\n"
        "fi\n"
        f"exit {install_exit}\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    return os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}"}


def _tree_hashes(root):
    return {
        path.relative_to(root): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_setup_dry_run_leaves_checkout_byte_identical_and_prints_commands(tmp_path):
    checkout = _setup_checkout(tmp_path)
    before = _tree_hashes(checkout)

    result = subprocess.run(
        [checkout / "scripts" / "contribute", "setup", "--dry-run"],
        cwd=checkout,
        env=_setup_env(tmp_path, checkout),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "python3 -m venv .venv" in result.stdout
    assert ".venv/bin/python -m pip install -r requirements-dev.txt" in result.stdout
    assert _tree_hashes(checkout) == before


def test_setup_creates_venv_then_installs_dev_requirements(tmp_path):
    checkout = _setup_checkout(tmp_path)

    result = subprocess.run(
        [checkout / "scripts" / "contribute", "setup"],
        cwd=checkout,
        env=_setup_env(tmp_path, checkout),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (checkout / "python3.args").read_text(encoding="utf-8").splitlines() == [
        "-m venv .venv",
        "-m pip install -r requirements-dev.txt",
    ]


def test_setup_twice_does_not_recreate_existing_venv(tmp_path):
    checkout = _setup_checkout(tmp_path)
    env = _setup_env(tmp_path, checkout)

    first = subprocess.run(
        [checkout / "scripts" / "contribute", "setup"],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    first_commands = (
        (checkout / "python3.args").read_text(encoding="utf-8").splitlines()
    )
    second = subprocess.run(
        [checkout / "scripts" / "contribute", "setup"],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    all_commands = (checkout / "python3.args").read_text(encoding="utf-8").splitlines()

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert all_commands[len(first_commands) :] == [
        "-m pip install -r requirements-dev.txt"
    ]


def test_setup_reports_failing_install_and_exits_nonzero(tmp_path):
    checkout = _setup_checkout(tmp_path)

    result = subprocess.run(
        [checkout / "scripts" / "contribute", "setup"],
        cwd=checkout,
        env=_setup_env(tmp_path, checkout, install_exit=7),
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "install failed" in output.lower()
    assert "success" not in output.lower()


def test_successful_setup_prints_activation_and_current_shell_warning(tmp_path):
    checkout = _setup_checkout(tmp_path)

    result = subprocess.run(
        [checkout / "scripts" / "contribute", "setup"],
        cwd=checkout,
        env=_setup_env(tmp_path, checkout),
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "source .venv/bin/activate" in output
    assert "current shell is unchanged" in output.lower()


def test_setup_never_writes_env_when_example_exists(tmp_path):
    checkout = _setup_checkout(tmp_path)
    (checkout / ".env.example").write_text(
        "POSTGRES_PASSWORD=your-secure-postgres-password\n"
        "SECRET_KEY=your-secret-key-here\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [checkout / "scripts" / "contribute", "setup"],
        cwd=checkout,
        env=_setup_env(tmp_path, checkout),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (checkout / ".env").exists()


def test_gate_invokes_canonical_gate_harness(tmp_path):
    checkout = _command_checkout(tmp_path)

    result = subprocess.run(
        [checkout / "scripts" / "contribute", "gate"],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (checkout / "test.sh.args").read_text(encoding="utf-8") == "gate\n"


def test_gate_forwards_nonzero_exit_code(tmp_path):
    checkout = _command_checkout(tmp_path, test_exit=7)

    result = subprocess.run(
        [checkout / "scripts" / "contribute", "gate"],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 7, result.stdout + result.stderr


def test_test_defaults_to_red_phase(tmp_path):
    checkout = _command_checkout(tmp_path)
    node = "tests/test_widget.py::test_rejects_invalid_widget"

    result = subprocess.run(
        [checkout / "scripts" / "contribute", "test", node],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (checkout / "tdd_cycle.sh.args").read_text(encoding="utf-8") == (
        f"red\n{node}\n"
    )


@pytest.mark.parametrize("phase", ["green", "refactor"])
def test_test_forwards_selected_phase(tmp_path, phase):
    checkout = _command_checkout(tmp_path)
    node = "tests/test_widget.py::test_accepts_widget"

    result = subprocess.run(
        [
            checkout / "scripts" / "contribute",
            "test",
            node,
            "--phase",
            phase,
        ],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (checkout / "tdd_cycle.sh.args").read_text(encoding="utf-8") == (
        f"{phase}\n{node}\n"
    )


def test_test_rejects_unrecognised_phase_and_names_valid_phases(tmp_path):
    checkout = _command_checkout(tmp_path)

    result = subprocess.run(
        [
            checkout / "scripts" / "contribute",
            "test",
            "tests/test_widget.py",
            "--phase",
            "maybe",
        ],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert all(phase in output for phase in ("red", "green", "refactor"))


def test_doctor_checks_bare_ruff_executable_at_pin(tmp_path):
    pin = _ruff_pin()
    result = subprocess.run(
        [CONTRIBUTE, "doctor"],
        cwd=PROJECT_ROOT,
        env=_fake_python(
            tmp_path,
            "3.12.0",
            ruff_version=pin,
            bare_ruff_version=pin,
            commands=("node", "npm", "mypy"),
        ),
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "ruff executable" in output
    assert pin in output


def test_doctor_rejects_disagreeing_ruff_sources(tmp_path):
    pin = _ruff_pin()
    stale = "0.15.20"
    result = subprocess.run(
        [CONTRIBUTE, "doctor"],
        cwd=PROJECT_ROOT,
        env=_fake_python(
            tmp_path,
            "3.12.0",
            ruff_version=pin,
            bare_ruff_version=stale,
            commands=("node", "npm", "mypy"),
        ),
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "ruff executable" in output
    assert "python3 -m ruff" in output
    assert pin in output
    assert stale in output


def test_doctor_rejects_missing_bare_ruff_when_module_works(tmp_path):
    pin = _ruff_pin()
    result = subprocess.run(
        [CONTRIBUTE, "doctor"],
        cwd=PROJECT_ROOT,
        env=_fake_python(
            tmp_path,
            "3.12.0",
            ruff_version=pin,
            bare_ruff_version=None,
            commands=("node", "npm", "mypy"),
            isolated_path=True,
        ),
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "ruff executable" in output
    assert "not found" in output
    assert f"python3 -m ruff {pin}" in output
    assert "python3 -m pip install -r requirements-dev.txt" in output


def test_doctor_reports_broken_ruff_executable_without_traceback(tmp_path):
    pin = _ruff_pin()
    env = _fake_python(
        tmp_path,
        "3.12.0",
        ruff_version=pin,
        bare_ruff_version=pin,
        commands=("node", "npm", "mypy"),
    )
    fake_bin = Path(env["PATH"].split(os.pathsep)[0])
    (fake_bin / "ruff").write_text("#!/missing/ruff-interpreter\n", encoding="utf-8")

    result = subprocess.run(
        [CONTRIBUTE, "doctor"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Traceback" not in output
    assert "ruff executable" in output


def test_check_rejects_forbidden_path_and_names_matching_pattern():
    result = subprocess.run(
        [CONTRIBUTE, "check", "strategy/notes.md"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "strategy/notes.md" in output
    assert "strategy/**" in output


def test_check_accepts_allowed_path():
    result = subprocess.run(
        [CONTRIBUTE, "check", "README.md"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_check_reports_every_forbidden_path_and_pattern():
    result = subprocess.run(
        [
            CONTRIBUTE,
            "check",
            "strategy/notes.md",
            "README.md",
            "private/brief.md",
            "AGENTS.md",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "strategy/notes.md" in output
    assert "strategy/**" in output
    assert "private/brief.md" in output
    assert "private/**" in output
    assert "AGENTS.md" in output


def _ci_forbidden_pattern(path, policy_file=None):
    if policy_file is None:
        policy_file = PROJECT_ROOT / ".github" / "forbidden-paths.txt"
    script = r"""
path_to_check=$1
policy_file=$2
while IFS= read -r pattern || [ -n "$pattern" ]; do
    policy_entry=$pattern
    while :; do
        case "$policy_entry" in
            [[:space:]]*) policy_entry=${policy_entry#?} ;;
            *) break ;;
        esac
    done
    case "$policy_entry" in
        ""|\#*) continue ;;
    esac
    if [[ "$path_to_check" == $pattern ]]; then
        printf '%s\n' "$pattern"
        exit 0
    fi
done < "$policy_file"
exit 1
"""
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            script,
            "bash",
            path,
            policy_file,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode in {0, 1}, result.stdout + result.stderr
    return result.stdout.strip() or None


@pytest.mark.parametrize("ignored_line", ["  # indented comment", " \t"])
def test_ci_oracle_ignores_policy_whitespace(tmp_path, ignored_line):
    policy = tmp_path / "forbidden-paths.txt"
    policy.write_text(f"{ignored_line}\nAGENTS.md\n", encoding="utf-8")

    assert _ci_forbidden_pattern(ignored_line, policy) is None
    assert _ci_forbidden_pattern("AGENTS.md", policy) == "AGENTS.md"


@pytest.mark.parametrize(
    "path",
    [
        "AGENTS.md",
        "README.md",
        "archive/notes.md",
        "archive/2026/notes.md",
        ".github/scripts/check.sh",
        ".github/scripts/release/check.sh",
        "research.md",
        "research-findings.md",
        "docs/launch-plan.md",
        "docs/releases/launch-plan.md",
        "deploy.sh",
        "src/deploy.sh",
    ],
)
def test_check_matches_ci_bash_pattern(path):
    result = subprocess.run(
        [CONTRIBUTE, "check", path, "--json"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    diagnostics = (
        "scripts/contribute check output:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert result.stdout.strip(), (
        f"scripts/contribute check returned no JSON\n{diagnostics}"
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(
            f"scripts/contribute check returned invalid JSON\n{diagnostics}",
            pytrace=False,
        )
    ci_pattern = _ci_forbidden_pattern(path)
    workbench_pattern = next(
        (
            violation["pattern"]
            for violation in payload["violations"]
            if violation["path"] == path
        ),
        None,
    )
    assert (result.returncode != 0) is (ci_pattern is not None)
    assert workbench_pattern == ci_pattern


def _check_checkout(tmp_path):
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    policy_directory = checkout / ".github"
    scripts.mkdir(parents=True)
    policy_directory.mkdir()
    shutil.copy(CONTRIBUTE, scripts / "contribute")
    shutil.copy(
        PROJECT_ROOT / "scripts" / "contributor_workbench.py",
        scripts / "contributor_workbench.py",
    )
    shutil.copy(
        PROJECT_ROOT / ".github" / "forbidden-paths.txt",
        policy_directory / "forbidden-paths.txt",
    )
    subprocess.run(["git", "init"], cwd=checkout, check=True, capture_output=True)
    return checkout


@pytest.mark.parametrize("arguments", [[], ["--staged"]])
def test_check_reads_staged_paths_from_temporary_repository(tmp_path, arguments):
    checkout = _check_checkout(tmp_path)
    forbidden = checkout / "strategy" / "notes.md"
    forbidden.parent.mkdir()
    forbidden.write_text("private notes\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "strategy/notes.md"],
        cwd=checkout,
        check=True,
        capture_output=True,
    )

    result = subprocess.run(
        [checkout / "scripts" / "contribute", "check", *arguments],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "strategy/notes.md" in output
    assert "strategy/**" in output


def test_check_fails_closed_when_policy_file_is_missing(tmp_path):
    checkout = _check_checkout(tmp_path)
    (checkout / ".github" / "forbidden-paths.txt").unlink()

    result = subprocess.run(
        [checkout / "scripts" / "contribute", "check", "README.md"],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "forbidden-paths.txt" in output
    assert "Traceback" not in output


def test_check_fails_closed_when_policy_file_is_undecodable(tmp_path):
    checkout = _check_checkout(tmp_path)
    (checkout / ".github" / "forbidden-paths.txt").write_bytes(b"\xff")

    result = subprocess.run(
        [checkout / "scripts" / "contribute", "check", "README.md"],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "forbidden-paths.txt" in output
    assert "Traceback" not in output


def test_check_reads_new_policy_pattern_at_runtime(tmp_path):
    checkout = _check_checkout(tmp_path)
    policy = checkout / ".github" / "forbidden-paths.txt"
    with policy.open("a", encoding="utf-8") as handle:
        handle.write("\ncustom-private/**\n")

    result = subprocess.run(
        [
            checkout / "scripts" / "contribute",
            "check",
            "custom-private/notes.md",
        ],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "custom-private/notes.md" in output
    assert "custom-private/**" in output


def test_doctor_json_emits_only_parseable_structured_checks(tmp_path):
    result = subprocess.run(
        [CONTRIBUTE, "doctor", "--json"],
        cwd=PROJECT_ROOT,
        env=_fake_python(
            tmp_path,
            "3.12.0",
            ruff_version=_ruff_pin(),
            commands=("node", "npm", "mypy"),
        ),
        capture_output=True,
        text=True,
        check=False,
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert payload["checks"]
    assert all(
        {"group", "name", "ok", "detail"} <= check.keys() for check in payload["checks"]
    )


def test_check_json_emits_path_pattern_violation_objects():
    result = subprocess.run(
        [
            CONTRIBUTE,
            "check",
            "--json",
            "strategy/notes.md",
            "private/brief.md",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    payload = json.loads(result.stdout)
    assert result.returncode != 0
    assert result.stderr == ""
    assert payload["violations"] == [
        {"path": "strategy/notes.md", "pattern": "strategy/**"},
        {"path": "private/brief.md", "pattern": "private/**"},
    ]


def test_json_and_human_commands_use_identical_exit_codes(tmp_path):
    env = _fake_python(
        tmp_path,
        "3.12.0",
        ruff_version=_ruff_pin(),
        commands=("node", "npm", "mypy"),
    )
    doctor_human = subprocess.run(
        [CONTRIBUTE, "doctor"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    doctor_json = subprocess.run(
        [CONTRIBUTE, "doctor", "--json"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    check_human = subprocess.run(
        [CONTRIBUTE, "check", "strategy/notes.md"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    check_json = subprocess.run(
        [CONTRIBUTE, "check", "--json", "strategy/notes.md"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert doctor_json.returncode == doctor_human.returncode
    assert check_json.returncode == check_human.returncode


def test_glossary_and_tour_json_emit_context_data():
    glossary = subprocess.run(
        [CONTRIBUTE, "glossary", "--json"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    tour = subprocess.run(
        [CONTRIBUTE, "tour", "--json"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    glossary_payload = json.loads(glossary.stdout)
    tour_payload = json.loads(tour.stdout)
    assert glossary.returncode == 0, glossary.stdout + glossary.stderr
    assert tour.returncode == 0, tour.stdout + tour.stderr
    assert glossary.stderr == ""
    assert tour.stderr == ""
    assert glossary_payload["terms"] == [
        {"term": term, "meaning": meaning} for term, meaning in _domain_terms().items()
    ]
    assert [item["module"] for item in tour_payload["store_modules"]] == (
        _store_modules()
    )
    assert all("purpose" in item for item in tour_payload["store_modules"])


def test_help_names_contributor_command_not_python_module():
    result = subprocess.run(
        [CONTRIBUTE, "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "scripts/contribute" in output
    assert "contributor_workbench.py" not in output
