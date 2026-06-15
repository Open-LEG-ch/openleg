# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ensure private-only content is not tracked in the public repository."""

import os
import subprocess
from pathlib import Path


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
        "openclaw/config/openclaw.json",
        "research_academic_partnerships.md",
        "research_bfe_grants.md",
        "scripts/ralph_loop.py",
        "strategy-assessment-2026-03-26.md",
        "tests/test_ralph_loop.py",
    }
    assert tracked.isdisjoint(explicit_private)
    assert all(not path.startswith(".github/scripts/") for path in tracked)
    assert all(not path.startswith(".orch/") for path in tracked)
    assert all(not path.startswith("grants/") for path in tracked)
    assert all(not path.startswith("openclaw/config/cron/") for path in tracked)
    assert all(not path.startswith("overnight/") for path in tracked)
    assert all(not path.startswith("outreach/") for path in tracked)
    assert all(not path.startswith("prd/") for path in tracked)


def test_tracked_text_has_no_known_private_identifiers_or_secret_placeholders():
    forbidden_fragments = (
        "w" + "gusta",
        "baden" + "leg",
        "sk-" + "ant-",
        "/" + "Users/",
    )
    offenders = {}

    # Operator docs (CLAUDE.md, AGENTS.md) may reference real infra names.
    operator_docs = {"CLAUDE.md", "AGENTS.md"}

    for relative_path in sorted(_tracked_files()):
        if relative_path in operator_docs:
            continue
        path = Path(PROJECT_ROOT, relative_path)
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        matches = [
            item for item in forbidden_fragments if item.lower() in content.lower()
        ]
        if matches:
            offenders[relative_path] = matches

    assert offenders == {}


def test_public_package_excludes_private_workspace_material(tmp_path):
    destination = tmp_path / "openleg-public"
    subprocess.run(
        ["bash", "scripts/package_public.sh", str(destination)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    blocked_paths = {
        ".github/CODEOWNERS",
        "AGENTS.md",
        "CLAUDE.md",
        "SKILLS.md",
        "docs/openleg-open-source-standard.md",
        "docs/research.md",
        "insights_engine.py",
        "openclaw/Dockerfile",
        "openclaw/entrypoint.sh",
        "openclaw/mcp-openleg-server/package.json",
        "openclaw/mcp-openleg-server/server.mjs",
        "sales_pipeline.py",
        "templates/admin/ops.html",
        "templates/admin/pipeline.html",
        "templates/admin/strategy.html",
        "tests/test_admin_ops.py",
        "templates/emails/municipality_outreach.html",
        "tests/test_admin_pipeline.py",
        "tests/test_admin_strategy.py",
        "tests/test_demand_signal.py",
        "tests/test_e2e_integration.py",
        "tests/test_lea_reports.py",
        "tests/test_municipality_outreach.py",
        "tests/test_municipality_targeting.py",
        "tests/test_sales_pipeline.py",
    }
    packaged = {
        str(path.relative_to(destination))
        for path in destination.rglob("*")
        if path.is_file()
    }
    assert packaged.isdisjoint(blocked_paths)
    assert all(not path.startswith("openclaw/config/cron/") for path in packaged)
    assert all(not path.startswith("overnight/") for path in packaged)
    assert all(not path.startswith("prd/") for path in packaged)

    forbidden_fragments = (
        "w" + "gusta",
        "baden" + "leg",
        "sk-" + "ant-",
        "/" + "Users/",
    )
    offenders = {}
    for relative_path in sorted(packaged):
        path = destination / relative_path
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        matches = [
            item for item in forbidden_fragments if item.lower() in content.lower()
        ]
        if matches:
            offenders[relative_path] = matches
    assert offenders == {}


def test_public_package_has_no_private_operator_surfaces(tmp_path):
    destination = tmp_path / "openleg-public"
    subprocess.run(
        ["bash", "scripts/package_public.sh", str(destination)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    app_content = (destination / "app.py").read_text(encoding="utf-8")
    email_content = (destination / "email_automation.py").read_text(encoding="utf-8")
    database_content = (destination / "database.py").read_text(encoding="utf-8")
    compose_content = (destination / "docker-compose.yml").read_text(encoding="utf-8")

    forbidden_app_fragments = (
        "/admin/pipeline",
        "/admin/ops",
        "/admin/strategy",
        "/api/internal/agentmail",
        "/api/internal/lea-report",
        "/api/internal/ops-snapshot",
        "/admin/lea-reports",
    )
    assert all(fragment not in app_content for fragment in forbidden_app_fragments)
    assert "municipality_outreach" not in email_content
    assert "lea_reports" not in database_content
    assert "ops_snapshots" not in database_content
    assert "vnb_research" not in database_content
    assert "openclaw:" not in compose_content
