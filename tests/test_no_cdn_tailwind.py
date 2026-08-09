# SPDX-License-Identifier: AGPL-3.0-or-later
"""No template may depend on the Tailwind CDN; pages use the built CSS."""

import os
import re
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Matches the CDN script include regardless of protocol or attribute order.
TAILWIND_CDN_PATTERN = re.compile(r"https?://cdn\.tailwindcss\.com")


def test_no_template_uses_tailwind_cdn():
    offenders = []
    for path in Path(PROJECT_ROOT, "templates").rglob("*.html"):
        if TAILWIND_CDN_PATTERN.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(PROJECT_ROOT)))
    assert offenders == []


def test_standalone_pages_load_built_stylesheet():
    # Either a direct link to the built CSS or the shared brand partial
    # (which links it) satisfies the contract.
    accepted = ("/static/css/openleg.css", "partials/tailwind_brand.html")
    for relative in (
        "templates/utility/login.html",
        "templates/utility/register.html",
        "templates/utility/dashboard.html",
        "templates/admin/ops.html",
    ):
        content = Path(PROJECT_ROOT, relative).read_text(encoding="utf-8")
        assert any(marker in content for marker in accepted), relative
