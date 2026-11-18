# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for scripts/check_requirements.py."""

import importlib.util
import os
import subprocess
import sys
from importlib import metadata

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(PROJECT_ROOT, "scripts", "check_requirements.py")

_spec = importlib.util.spec_from_file_location("check_requirements", SCRIPT_PATH)
check_requirements = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_requirements)

PYTEST_VERSION = metadata.version("pytest")


def run_check(path, env=None):
    return subprocess.run(
        [sys.executable, SCRIPT_PATH, str(path)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def fake_distribution(tmp_path, name, version):
    """Liefert eine Umgebung, in der ``name`` mit genau ``version`` installiert ist."""
    site = tmp_path / "site"
    dist_info = site / f"{name.replace('-', '_')}-{version}.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    )
    return {**os.environ, "PYTHONPATH": str(site)}


def test_only_plain_pins_are_read():
    text = """# Kommentar

Flask==3.1.0  # Web
qrcode == 8.2
psycopg[binary]==3.2.0
tomli==2.0.1; python_version < '3.11'
requests>=2.0
click==8.*
jinja2==3.1.0,>=3
-r other.txt
"""
    assert check_requirements.pinned_requirements(text) == [
        ("Flask", "3.1.0"),
        ("qrcode", "8.2"),
    ]


def test_matching_environment_passes(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text(f"pytest=={PYTEST_VERSION}\n")

    result = run_check(req)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_names_are_normalized(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text(f"PyTest=={PYTEST_VERSION}\n")

    assert run_check(req).returncode == 0


def test_equivalent_version_spelling_passes(tmp_path):
    """pip treats ``==X.Y`` and ``==X.Y.0`` as the same version.

    A text comparison would keep failing after ``pip install`` reports that
    everything is already installed, so the pipeline could never start.
    """
    env = fake_distribution(tmp_path, "openleg-fake", "1.2")
    req = tmp_path / "requirements.txt"
    req.write_text("openleg-fake==1.2.0\n")

    result = run_check(req, env)

    assert result.returncode == 0, result.stderr


def test_public_pin_accepts_a_local_build():
    """pip accepts ``1.0+local`` for ``==1.0``, so the check must too."""
    assert check_requirements._satisfies("1.0+local", "1.0")
    assert not check_requirements._satisfies("1.0.1", "1.0")


def test_missing_package_is_reported(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("surely-not-installed-openleg-pkg==1.0\n")

    result = run_check(req)

    assert result.returncode == 1
    assert "surely-not-installed-openleg-pkg==1.0 (fehlt)" in result.stderr


def test_version_mismatch_is_reported(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("pytest==0.0.1\n")

    result = run_check(req)

    assert result.returncode == 1
    assert f"pytest==0.0.1 (installiert: {PYTEST_VERSION})" in result.stderr


def test_unreadable_requirements_file_is_an_input_error(tmp_path):
    """A missing file is not a stale venv, so it must not exit like one."""
    result = run_check(tmp_path / "fehlt.txt")

    assert result.returncode == 2
    assert "fehlt.txt" in result.stderr
    assert "Traceback" not in result.stderr
