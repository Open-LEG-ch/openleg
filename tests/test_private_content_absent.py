# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ensure private-only content is not tracked in the public repository.

The public boundary is content-based: internal business machinery (strategy
dashboards, sales pipeline, municipality outreach) lives in the private ops
repo. Generic, token-gated instance ops (ops snapshots, LEA reports, internal
automation endpoints) stays public as part of the product runtime.
"""

import os
import re
import subprocess
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Internal business machinery: never tracked here. Local overlays of these
# paths must stay untracked via .gitignore.
PRIVATE_ONLY_PATHS = (
    "insights_engine.py",
    "sales_pipeline.py",
    "templates/admin/strategy.html",
    "templates/admin/pipeline.html",
    "templates/emails/municipality_outreach.html",
    "tests/test_admin_strategy.py",
    "tests/test_admin_pipeline.py",
    "tests/test_sales_pipeline.py",
    "tests/test_municipality_outreach.py",
    "tests/test_municipality_targeting.py",
    "tests/test_demand_signal.py",
    "docs/matt-pocock-quality-slices.md",
    "scripts/package_public.sh",
    "scripts/sanitize_public_snapshot.py",
)


def _tracked_files():
    cached = subprocess.run(
        ["git", "ls-files"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    deleted = subprocess.run(
        ["git", "ls-files", "--deleted"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    tracked = {line.strip() for line in cached.stdout.splitlines() if line.strip()}
    removed = {line.strip() for line in deleted.stdout.splitlines() if line.strip()}
    return tracked - removed


def test_private_assets_are_not_tracked():
    tracked = _tracked_files()
    explicit_private = {
        "PIVOT.md",
        "PLAN.md",
        ".github/CODEOWNERS",
        "docs/openleg-open-source-standard.md",
        "docs/research.md",
        "open-strategy.md",
        "research_academic_partnerships.md",
        "research_bfe_grants.md",
        "scripts/ralph_loop.py",
        "strategy-assessment-2026-03-26.md",
        "tests/test_ralph_loop.py",
    } | set(PRIVATE_ONLY_PATHS)
    assert tracked.isdisjoint(explicit_private)
    assert all(not path.startswith(".github/scripts/") for path in tracked)
    assert all(not path.startswith(".orch/") for path in tracked)
    assert all(not path.startswith("grants/") for path in tracked)
    assert all(not path.startswith("overnight/") for path in tracked)
    assert all(not path.startswith("outreach/") for path in tracked)
    assert all(not path.startswith("prd/") for path in tracked)


def test_private_only_paths_are_gitignored():
    """Private overlays of the removed paths must stay untracked locally."""
    unignored = []
    for path in PRIVATE_ONLY_PATHS:
        result = subprocess.run(
            ["git", "check-ignore", "-q", path],
            cwd=PROJECT_ROOT,
            check=False,
        )
        if result.returncode != 0:
            unignored.append(path)
    assert unignored == []


def test_no_public_snapshot_markers_in_tracked_sources():
    """The snapshot-sanitizer era is over: no marker sections may remain."""
    marker = "PUBLIC-SNAPSHOT" + "-PRIVATE"
    this_file = Path(__file__).resolve()
    offenders = []
    for relative_path in sorted(_tracked_files()):
        path = Path(PROJECT_ROOT, relative_path)
        if not path.is_file() or path.resolve() == this_file:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if marker in content:
            offenders.append(relative_path)
    assert offenders == []


def test_private_operator_surface_absent_from_modules():
    app_content = Path(PROJECT_ROOT, "app.py").read_text(encoding="utf-8")
    admin_content = Path(PROJECT_ROOT, "admin.py").read_text(encoding="utf-8")
    for fragment in ("/admin/pipeline", "/admin/strategy", "insights_engine"):
        assert fragment not in app_content
        assert fragment not in admin_content

    email_content = Path(PROJECT_ROOT, "email_automation.py").read_text(
        encoding="utf-8"
    )
    assert "municipality_outreach" not in email_content
    assert "insights_engine" not in email_content

    database_content = Path(PROJECT_ROOT, "database.py").read_text(encoding="utf-8")
    for fragment in ("vnb_pipeline", "vnb_research", "def save_insight"):
        assert fragment not in database_content


def test_kept_instance_ops_schema_is_provisioned():
    """The kept ops surface needs its tables created by init_db."""
    schema_content = Path(PROJECT_ROOT, "store", "schema.py").read_text(
        encoding="utf-8"
    )
    for fragment in (
        "CREATE TABLE IF NOT EXISTS lea_reports",
        "CREATE TABLE IF NOT EXISTS ops_snapshots",
    ):
        assert fragment in schema_content


_METERING_POINT = re.compile(r"\bCH[A-Z0-9]{20,}\b")
_COMMUNITY_ID = re.compile(r"(?<![A-Z0-9])\d{6}-[A-Z0-9]{6}(?![A-Z0-9])")
_HOME_PATH = re.compile(r"/(?:Users|home)/[^/\s]+(?=/|\s|$)")
_SECRET_TOKEN = re.compile(r"\bsk-(?:ant|live|proj)-[A-Za-z0-9_-]{16,}\b")
_KNOWN_PRIVATE_FRAGMENTS = (
    "w" + "gusta",
    "baden" + "leg",
)


def _is_synthetic_metering_point(value):
    body = value.removeprefix("CH")
    return len(body) >= 3 and body[:-2].strip("0") == "" and body[-2:].isdigit()


def _private_markers(content):
    markers = []
    if any(
        not _is_synthetic_metering_point(match.group())
        for match in _METERING_POINT.finditer(content)
    ):
        markers.append("metering point")
    if _COMMUNITY_ID.search(content):
        markers.append("CommunityID")
    if _HOME_PATH.search(content):
        markers.append("home path")
    if _SECRET_TOKEN.search(content):
        markers.append("secret token")
    if any(fragment in content.lower() for fragment in _KNOWN_PRIVATE_FRAGMENTS):
        markers.append("known private identifier")
    return markers


def test_private_marker_scan_detects_identifier_shapes_without_echoing_values():
    samples = {
        "metering point": "CH" + "7" * 30,
        "CommunityID": "1" * 6 + "-" + "A" * 6,
        "home path": "/" + "Users/" + "example/project",
        "secret token": "sk-" + "ant-" + "x" * 24,
    }

    for category, sample in samples.items():
        assert _private_markers(sample) == [category]


def test_private_marker_scan_detects_home_directory_at_nonfinal_line_end():
    content = "Pfad: /" + "Users/example\nWeitere Zeile"
    assert _private_markers(content) == ["home path"]


def test_private_marker_scan_detects_known_legacy_fragments():
    samples = (
        "w" + "gusta",
        "baden" + "leg",
    )

    for sample in samples:
        assert _private_markers(sample) == ["known private identifier"]


def test_private_marker_scan_allows_synthetic_ids_and_lowercase_url_paths():
    synthetic_point = "CH" + "0" * 28 + "01"
    assert _private_markers(synthetic_point) == []
    assert _private_markers("https://example.ch/users/notifications") == []


def test_tracked_text_has_no_known_private_identifiers_or_secret_placeholders():
    offenders = {}

    for relative_path in sorted(_tracked_files()):
        path = Path(PROJECT_ROOT, relative_path)
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        matches = _private_markers(content)
        if matches:
            offenders[relative_path] = matches

    assert offenders == {}
