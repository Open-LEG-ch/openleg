# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rendered release layouts and colour contrast."""

from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest

from tests.test_contrast import contrast
from tests.test_i18n import _synthetic_environment


@pytest.fixture
def release_page():
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
                client = web.test_client()

                def route(request):
                    url = urlsplit(request.request.url)
                    if url.netloc != "qa.test":
                        request.abort()
                        return
                    if url.path.startswith("/static/"):
                        asset = root / url.path.lstrip("/")
                        if asset.is_file():
                            request.fulfill(path=str(asset))
                            return
                    response = client.get(url.path, query_string=url.query)
                    request.fulfill(
                        status=response.status_code,
                        body=response.text,
                        content_type="text/html",
                    )

                page.route("**/*", route)
                yield page
            finally:
                browser.close()


def test_german_software_roles_fit_tablet_viewport(release_page):
    page = release_page
    assert page.goto("http://qa.test/software?lang=de-CH").status == 200
    assert page.locator(".release-practice-roles").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth") == 768
    page.set_viewport_size({"width": 1280, "height": 900})
    # The navy band keeps 48 px of inner padding on each side at desktop width.
    assert page.locator(".release-software-band").bounding_box()["width"] == 1184
    assert page.locator(".release-workflow").bounding_box()["width"] == 1088
    assert page.locator(".release-software-band").evaluate(
        """el => {
            const style = getComputedStyle(el);
            return [style.backgroundColor, style.borderTopColor, style.borderTopWidth];
        }"""
    ) == ["rgb(36, 59, 133)", "rgb(203, 129, 92)", "8px"]


def test_install_console_indicators_contrast_on_dark_surfaces(release_page):
    page = release_page
    assert page.goto("http://qa.test/self-host").status == 200
    console = page.locator("[data-install-console]")
    for name in ("direct", "inspect", "git"):
        tab = console.locator(f"#install-tab-{name}")
        if name != "direct":
            tab.click()
        assert tab.get_attribute("aria-selected") == "true"
        copy = console.locator(f"#install-{name} [data-copy-command]")
        copy.hover()
        for control, backdrop in (
            (tab, "[data-install-console]"),
            (copy, ".bg-slate-950"),
        ):
            foreground, background = control.evaluate(
                """(el, backdrop) => {
                    const ctx = document.createElement("canvas").getContext("2d");
                    function asHex(colour) {
                        ctx.clearRect(0, 0, 1, 1);
                        ctx.fillStyle = colour;
                        ctx.fillRect(0, 0, 1, 1);
                        return Array.from(ctx.getImageData(0, 0, 1, 1).data)
                            .slice(0, 3).map(v => v.toString(16).padStart(2, "0")).join("");
                    }
                    const border = getComputedStyle(el).borderBottomColor;
                    const surface = getComputedStyle(el.closest(backdrop)).backgroundColor;
                    return [asHex(border), asHex(surface)];
                }""",
                backdrop,
            )
            assert foreground == "e5a07a"
            assert contrast(foreground, background) >= 3
        assert tab.evaluate("el => getComputedStyle(el).color") == "rgb(255, 255, 255)"


def test_release_product_readiness_text_is_white(release_page):
    page = release_page
    assert page.goto("http://qa.test/dashboard/demo").status == 200
    panel = page.locator(".product-shell section[aria-labelledby='readiness-title']")
    assert (
        panel.locator("[data-statistic].text-6xl").evaluate(
            "el => getComputedStyle(el).color"
        )
        == "rgb(255, 255, 255)"
    )
    assert (
        panel.locator("[role='progressbar'] > span").evaluate(
            "el => getComputedStyle(el).backgroundColor"
        )
        == "rgb(238, 141, 124)"
    )
    assert (
        panel.locator("#readiness-title").evaluate("el => getComputedStyle(el).color")
        == "rgb(255, 255, 255)"
    )


DARK_SURFACE_TEXT = """el => {
    const ctx = document.createElement("canvas").getContext("2d");
    const rgb = colour => {
        ctx.clearRect(0, 0, 1, 1);
        ctx.fillStyle = colour;
        ctx.fillRect(0, 0, 1, 1);
        return Array.from(ctx.getImageData(0, 0, 1, 1).data);
    };
    const linear = v => (v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    const luminance = ([r, g, b]) => 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
    const backdrop = node => {
        for (; node; node = node.parentElement) {
            const colour = rgb(getComputedStyle(node).backgroundColor);
            if (colour[3] > 128) return colour;
        }
        return [255, 255, 255, 255];
    };
    const found = new Map();
    for (const node of [el, ...el.querySelectorAll("*")]) {
        const text = [...node.childNodes]
            .filter(child => child.nodeType === 3 && child.textContent.trim())
            .map(child => child.textContent.trim()).join(" ");
        if (!text || !node.getClientRects().length) continue;
        if (luminance(backdrop(node)) > 0.2) continue;
        found.set(getComputedStyle(node).color, text.slice(0, 40));
    }
    return Object.fromEntries(found);
}"""


@pytest.mark.parametrize(
    "path, surface",
    [
        ("/", ".home-practice"),
        ("/software", ".release-software-band"),
        ("/self-host", "[data-install-console]"),
        ("/", ".release-footer"),
    ],
)
def test_text_on_dark_surfaces_is_white(release_page, path, surface):
    page = release_page
    assert page.goto("http://qa.test" + path + "?lang=de-CH").status == 200
    colours = page.locator(surface).first.evaluate(DARK_SURFACE_TEXT)
    assert colours
    assert set(colours) <= {"rgb(255, 253, 248)", "rgb(255, 255, 255)"}, colours


@pytest.mark.parametrize("locale", ["de-CH", "fr-CH", "it-CH", "en"])
def test_desktop_header_labels_stay_on_one_line(release_page, locale):
    """French labels wrapped once the GitHub button took room; each must fit."""
    page = release_page
    for width in (1100, 1150, 1200, 1280, 1300, 1440):
        page.set_viewport_size({"width": width, "height": 800})
        assert page.goto(f"http://qa.test/?lang={locale}").status == 200
        assert page.evaluate("document.documentElement.scrollWidth") == width
        heights = page.evaluate(
            """() => [...document.querySelectorAll(
                '.release-nav-group > summary, .release-login, .release-github'
            )].map(el => Math.round(el.getBoundingClientRect().height))"""
        )
        assert heights and max(heights) <= 46, (width, heights)
