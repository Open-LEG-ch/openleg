# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guard tests: no legacy amber brand remnants in templates, static JS,
static SVGs, the source CSS, or the tenant color defaults."""

import glob
import os
import re

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_CSS = os.path.join(PROJECT_ROOT, "static", "css", "tailwind.css")
STATIC_JS_GLOB = os.path.join(PROJECT_ROOT, "static", "js", "*.js")
STATIC_SVG_GLOB = os.path.join(PROJECT_ROOT, "static", "**", "*.svg")
TEMPLATE_GLOB = os.path.join(PROJECT_ROOT, "templates", "**", "*.html")

FORBIDDEN_AMBER = re.compile(
    r"\bamber-\d+\b"
    r"|#f59e0b|#fbbf24|#fcd34d|#fef3c7|#fffbeb|#d97706|#ffc043|#92400e|#fde68a"
    r"|245,\s*158,\s*11",
    flags=re.IGNORECASE,
)


def _scanned_files():
    files = sorted(glob.glob(TEMPLATE_GLOB, recursive=True))
    files.extend(sorted(glob.glob(STATIC_JS_GLOB)))
    files.extend(sorted(glob.glob(STATIC_SVG_GLOB, recursive=True)))
    files.append(SOURCE_CSS)
    return files


def test_no_amber_in_templates_js_svgs_or_source_css():
    offenders = []
    for path in _scanned_files():
        with open(path, encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, 1):
                if FORBIDDEN_AMBER.search(line):
                    rel = os.path.relpath(path, PROJECT_ROOT)
                    offenders.append(f"{rel}:{lineno}: {line.strip()[:120]}")
    assert not offenders, (
        "legacy amber brand remnants found (use the violet brand tokens "
        "from tailwind.config.js instead):\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize(
    "module_path", ["tenant.py", os.path.join("store", "tenant.py")]
)
def test_tenant_default_colors_are_brand_tokens(module_path):
    with open(os.path.join(PROJECT_ROOT, module_path), encoding="utf-8") as handle:
        content = handle.read()
    assert not FORBIDDEN_AMBER.search(content), (
        f"{module_path} still defaults a tenant color to legacy amber"
    )
