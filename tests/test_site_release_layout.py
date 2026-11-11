# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rendered release pages fit a tablet viewport with configured sandbox roles."""

from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest

from tests.test_i18n import _synthetic_environment


def test_german_software_roles_fit_tablet_viewport():
    playwright = pytest.importorskip("playwright.sync_api")
    root = Path(__file__).resolve().parents[1]
    with _synthetic_environment():
        import app
        import database
        import homepage_view_model

        web = app.create_app(
            {
                "TESTING": True,
                "SITE_RELEASE_ENABLED": True,
                "SECRET_KEY": "layout-test",
                "RATELIMIT_STORAGE_URI": "memory://",
            },
            load_environment=False,
            check_database=False,
        )
        web.jinja_env.globals["sandbox_url"] = "https://sandbox.example.invalid/sandbox"
        with (
            patch.object(
                database, "get_stats", lambda city_id=None: {"total_buildings": 0}
            ),
            patch.object(homepage_view_model, "ranking_extremes", lambda: ([], [], 0)),
            playwright.sync_playwright() as driver,
        ):
            browser = driver.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                page = browser.new_page(viewport={"width": 768, "height": 900})
                response = web.test_client().get("/software?lang=de-CH")
                assert response.status_code == 200

                def route(request):
                    url = urlsplit(request.request.url)
                    if url.netloc == "qa.test" and url.path.startswith("/static/"):
                        asset = root / url.path.lstrip("/")
                        if asset.is_file():
                            request.fulfill(path=str(asset))
                            return
                    if url.netloc == "qa.test" and url.path == "/software":
                        request.fulfill(body=response.text, content_type="text/html")
                        return
                    request.abort()

                page.route("**/*", route)
                page.goto("http://qa.test/software?lang=de-CH")
                assert page.locator(".release-practice-roles").is_visible()
                assert page.evaluate("document.documentElement.scrollWidth") == 768
            finally:
                browser.close()
