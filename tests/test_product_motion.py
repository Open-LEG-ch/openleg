# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contracts for real product motion and nearby self-service actions (#286)."""

from pathlib import Path

import pytest
from PIL import Image

PROJECT_ROOT = Path(__file__).parents[1]
ASSET_DIR = PROJECT_ROOT / "static" / "images" / "product"

FLOWS = {
    "resident-dashboard": "/dashboard/demo",
    "municipality-dashboard": "/gemeinde/dashboard/demo",
    "leg-formation": "/leg/dashboard/demo",
}


@pytest.mark.parametrize("name", FLOWS)
def test_product_flow_has_real_animated_gif_and_static_fallback(name):
    gif_path = ASSET_DIR / f"{name}.gif"
    fallback_path = ASSET_DIR / f"{name}-static.webp"

    assert gif_path.is_file()
    assert fallback_path.is_file()
    assert gif_path.stat().st_size < 5_000_000
    with Image.open(gif_path) as animation:
        assert animation.format == "GIF"
        assert animation.n_frames >= 4
        assert animation.info.get("duration", 0) >= 200
    with Image.open(fallback_path) as fallback:
        assert fallback.format == "WEBP"


@pytest.mark.parametrize(
    ("template", "flow", "demo_path", "action_path"),
    [
        ("index.html", "resident-dashboard", "/dashboard/demo", "/#registrieren"),
        (
            "fuer_bewohner.html",
            "resident-dashboard",
            "/dashboard/demo",
            "/#registrieren",
        ),
        (
            "fuer_gemeinden.html",
            "municipality-dashboard",
            "/gemeinde/dashboard/demo",
            "/gemeinde/onboarding",
        ),
        (
            "leg_gruenden.html",
            "leg-formation",
            "/leg/dashboard/demo",
            "/#registrieren",
        ),
        ("open_source.html", "leg-formation", "/leg/dashboard/demo", "/self-host"),
    ],
)
def test_motion_is_placed_beside_demo_and_self_service_actions(
    template, flow, demo_path, action_path
):
    source = (PROJECT_ROOT / "templates" / template).read_text(encoding="utf-8")

    assert f"/static/images/product/{flow}.gif" in source
    assert f"/static/images/product/{flow}-static.webp" in source
    assert f'href="{demo_path}"' in source
    assert f'href="{action_path}"' in source
    assert 'loading="lazy"' in source
    assert 'width="960" height="540"' in source
    assert "prefers-reduced-motion: reduce" in source
