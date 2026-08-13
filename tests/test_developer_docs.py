# SPDX-License-Identifier: AGPL-3.0-or-later
"""Public developer documentation and tooling contracts (#287)."""

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_frontend_build_is_pinned_and_documented():
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert package["name"] == "openleg"
    assert package["private"] is True
    assert package["scripts"]["build:css"] == (
        "tailwindcss -i static/css/tailwind.css -o static/css/openleg.css --minify"
    )
    assert package["devDependencies"]["tailwindcss"] == "3.4.17"
    assert (ROOT / "package-lock.json").is_file()
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["name"] == "openleg"
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "npm ci" in agents
    assert "npm run build:css" in agents
    assert "npx tailwindcss" not in agents
    lint_workflow = (ROOT / ".github" / "workflows" / "lint.yml").read_text(
        encoding="utf-8"
    )
    assert "npm ci" in lint_workflow
    assert "npm run build:css" in lint_workflow
    assert "git diff --exit-code -- static/css/openleg.css" in lint_workflow


def test_secure_dashboard_access_is_publicly_documented():
    access = (ROOT / "docs" / "dashboard-access.md").read_text(encoding="utf-8")
    for text in (
        "DASHBOARD_ACCESS_TOKEN_TTL_SECONDS",
        "900",
        "24 hours",
        "one-time",
        "SHA-256",
        "HttpOnly",
        "SameSite=Lax",
        "Cache-Control: no-store",
        "no-referrer",
        "CSRF",
        "/dashboard/demo",
        "/leg/dashboard/demo",
        "No production data",
    ):
        assert text in access
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/dashboard-access.md" in readme


def test_pull_request_template_pins_quality_and_security_checks():
    template = (ROOT / ".github" / "pull_request_template.md").read_text(
        encoding="utf-8"
    )
    for text in (
        "Closes #",
        "- [ ] RED: a focused test failed for the missing or broken behaviour",
        "- [ ] GREEN: the focused test passes",
        "- [ ] `scripts/tdd_cycle.sh gate` passes",
        "CodeRabbit",
        "- [ ] I tested user-facing changes in a real browser on desktop and mobile",
        "- [ ] This change contains no secrets, production host details or private operations notes",
        "- [ ] This change contains no citizen data or identifying smart-meter data",
    ):
        assert text in template
