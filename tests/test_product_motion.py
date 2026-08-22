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
        assert animation.info.get("loop") is None
        total_duration = 0
        for frame_number in range(animation.n_frames):
            animation.seek(frame_number)
            total_duration += animation.info.get("duration", 0)
        assert 1_000 <= total_duration <= 5_000
    with Image.open(fallback_path) as fallback:
        assert fallback.format == "WEBP"
