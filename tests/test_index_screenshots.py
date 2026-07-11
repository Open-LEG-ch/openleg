# SPDX-License-Identifier: AGPL-3.0-or-later
"""Homepage shows the backend: real screenshots, show-don't-tell contract."""

import os
import re
from unittest.mock import MagicMock, patch

import pytest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "static", "images", "screenshots")
MAX_BYTES = 250 * 1024


def _index_html():
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": "postgresql://x:x@localhost/x",
            "REDIS_URL": "memory://",
            "APP_BASE_URL": "http://localhost:5003",
        },
    ):
        with (
            patch("database.is_db_available", return_value=True),
            patch("database.init_db", return_value=True),
            patch("database._connection_pool", MagicMock()),
            patch("database.get_stats", return_value={"total_buildings": 7}),
        ):
            try:
                from app import app
            except Exception:
                pytest.skip("App import requires live DB")
            client = app.test_client()
            response = client.get("/")
            assert response.status_code == 200
            return response.get_data(as_text=True)


def test_homepage_has_product_section_with_screenshots():
    html = _index_html()
    assert 'id="produkt"' in html
    imgs = re.findall(r'<img[^>]+src="(/static/images/screenshots/[^"]+)"', html)
    assert len(imgs) >= 2


def test_screenshot_images_exist_and_are_within_budget():
    html = _index_html()
    referenced = re.findall(r'src="/static/images/screenshots/([^"]+)"', html)
    assert referenced
    for name in referenced:
        path = os.path.join(SCREENSHOT_DIR, name)
        assert os.path.isfile(path), f"missing screenshot {name}"
    for name in os.listdir(SCREENSHOT_DIR):
        size = os.path.getsize(os.path.join(SCREENSHOT_DIR, name))
        assert size < MAX_BYTES, f"{name} is {size} bytes, budget is {MAX_BYTES}"


def test_screenshot_imgs_have_alt_lazy_and_dimensions():
    html = _index_html()
    img_tags = re.findall(r"<img[^>]+/static/images/screenshots/[^>]+>", html)
    assert img_tags
    for tag in img_tags:
        assert re.search(r'alt="[^"]{5,}"', tag), tag
        assert 'loading="lazy"' in tag, tag
        assert re.search(r'width="\d+"', tag), tag
        assert re.search(r'height="\d+"', tag), tag


def test_product_section_links_to_live_demos():
    html = _index_html()
    assert 'href="/dashboard/demo"' in html
    assert 'href="/gemeinde/dashboard/demo"' in html
