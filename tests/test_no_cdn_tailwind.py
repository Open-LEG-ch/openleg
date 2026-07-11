# SPDX-License-Identifier: AGPL-3.0-or-later
"""No template may depend on the Tailwind CDN; pages use the built CSS."""

import os
from pathlib import Path


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_no_template_uses_tailwind_cdn():
    offenders = []
    for path in Path(PROJECT_ROOT, "templates").rglob("*.html"):
        if "cdn.tailwindcss.com" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(PROJECT_ROOT)))
    assert offenders == []


def test_standalone_pages_load_built_stylesheet():
    for relative in (
        "templates/utility/login.html",
        "templates/utility/register.html",
        "templates/utility/dashboard.html",
        "templates/admin/ops.html",
    ):
        content = Path(PROJECT_ROOT, relative).read_text(encoding="utf-8")
        assert "/static/css/openleg.css" in content, relative
