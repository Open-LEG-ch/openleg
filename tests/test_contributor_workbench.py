# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract tests for the contributor workbench CLI."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRIBUTE = PROJECT_ROOT / "scripts" / "contribute"

pytestmark = pytest.mark.contract


def _fake_python(
    tmp_path,
    version,
    *,
    pytest_present=True,
    ruff_version=None,
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
