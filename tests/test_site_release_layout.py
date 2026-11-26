# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rendered release layouts and colour contrast."""

from itertools import pairwise, product
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


# The search partial arrives in a separate change; this stands in for its field.
SEARCH_STAND_IN = """() => {
    const slot = document.querySelector('.release-search-slot');
    slot.innerHTML = '<form role="search"><input aria-label="Suche" style="width:200px;min-height:44px"></form>';
}"""

LINE = """el => {
    const line = getComputedStyle(el.querySelector('.release-nav-line'), '::after');
    return [line.backgroundColor, line.height, line.transform];
}"""
# The roof-coloured 2 px line under "LEG" in the wordmark, fully drawn or hidden.
LINE_SHOWN = ["rgb(203, 129, 92)", "2px", "none"]
LINE_HIDDEN = ["rgb(203, 129, 92)", "2px", "matrix(0, 0, 0, 1, 0, 0)"]


def _nav_ready(page, path, width=1280):
    page.set_viewport_size({"width": width, "height": 900})
    assert page.goto("http://qa.test" + path).status == 200
    page.wait_for_selector("[data-release-nav][data-enhanced='true']")


def test_navigation_flyout_spans_the_viewport_and_stays_open_while_hovered(
    release_page,
):
    page = release_page
    _nav_ready(page, "/?lang=de-CH")
    group = page.locator(".release-nav-group").nth(1)
    summary = group.locator("summary")
    assert summary.evaluate(LINE) == LINE_HIDDEN
    summary.hover()
    page.wait_for_timeout(400)
    assert group.evaluate("el => el.open")
    page.wait_for_timeout(300)
    assert summary.evaluate(LINE) == LINE_SHOWN, "the open item keeps the logo line"
    panel = group.locator(".release-nav-panel")
    box = panel.bounding_box()
    nav = page.locator(".release-nav").bounding_box()
    assert box["x"] == 0
    assert box["width"] == page.evaluate("document.documentElement.clientWidth")
    assert box["y"] == nav["y"] + nav["height"]
    # The links line up with the page content under the logo.
    logo = page.locator(".release-nav .ol-logo").bounding_box()
    assert panel.locator("a").first.bounding_box()["x"] == logo["x"]
    assert panel.evaluate(
        "el => [getComputedStyle(el).backgroundColor, getComputedStyle(el).borderBottom]"
    ) == ["rgb(255, 253, 248)", "1px solid rgb(188, 195, 208)"]
    assert page.locator(".release-nav-panel:visible").count() == 1
    # The pointer travels down across the bar into the panel and along its links.
    links = panel.locator("a")
    trigger = summary.bounding_box()
    x = trigger["x"] + trigger["width"] / 2
    page.mouse.move(x, nav["y"] + nav["height"] - 2, steps=3)
    page.mouse.move(x, box["y"] + 10, steps=3)
    links.first.hover()
    links.last.hover()
    page.wait_for_timeout(700)
    assert group.evaluate("el => el.open")
    page.mouse.move(640, 700, steps=4)
    page.wait_for_timeout(700)
    assert not group.evaluate("el => el.open")


def test_navigation_keyboard_opens_enters_and_escapes_a_flyout(release_page):
    page = release_page
    _nav_ready(page, "/pricing?lang=de-CH")
    summary = page.locator(".release-nav-group summary").first
    page.keyboard.press("Tab")
    summary.focus()
    page.wait_for_timeout(300)
    assert summary.evaluate(LINE) == LINE_SHOWN, "keyboard focus shows the logo line"
    page.keyboard.press("Enter")
    group = page.locator(".release-nav-group").first
    assert group.evaluate("el => el.open")
    page.keyboard.press("Tab")
    first_link = group.locator(".release-nav-panel a").first
    assert first_link.evaluate("el => el === document.activeElement")
    page.keyboard.press("Escape")
    assert not group.evaluate("el => el.open")
    assert summary.evaluate("el => el === document.activeElement")
    page.keyboard.press("ArrowDown")
    assert first_link.evaluate("el => el === document.activeElement")
    # The section of the current page keeps its line without hover or focus.
    current = page.locator(".release-nav-group[data-current-section] summary")
    page.keyboard.press("Escape")
    page.locator("main").click(position={"x": 5, "y": 400})
    page.wait_for_timeout(300)
    assert current.inner_text() == "Software"
    assert current.evaluate(LINE) == LINE_SHOWN
    other = page.locator(".release-nav-group:not([data-current-section]) summary").first
    assert other.evaluate(LINE) == LINE_HIDDEN


@pytest.mark.parametrize("lang", ["de-CH", "fr-CH", "it-CH", "en"])
def test_navigation_keeps_one_row_with_search_from_1100_px(release_page, lang):
    page = release_page
    # A page inside a section sets that label in bold, which makes it wider.
    pages = ("/", "/leg-gruenden", "/software", "/fuer-gemeinden", "/wissen", "/verein")
    for path, width in product(pages, (1100, 1200, 1280, 1440)):
        _nav_ready(page, f"{path}?lang={lang}", width)
        page.evaluate(SEARCH_STAND_IN)
        row = page.locator(".release-nav-row")
        tops = row.evaluate(
            """el => [el.querySelector('.ol-logo'), ...el.querySelectorAll(
                '.release-nav-group > summary, .release-search-slot, .release-login, .release-github'
            )].map(node => Math.round(node.getBoundingClientRect().top + node.getBoundingClientRect().height / 2))"""
        )
        assert max(tops) - min(tops) <= 2, (lang, path, width, tops)
        # Neighbours must not overlap, and the last one ends inside the container.
        edges = row.evaluate(
            """el => [el.querySelector('.ol-logo'), ...el.querySelectorAll(
                '.release-nav-group > summary, .release-search-slot input, .release-login, .release-github'
            )].map(node => [node.getBoundingClientRect().left, node.getBoundingClientRect().right])"""
        )
        edges.sort()
        for (_, right), (left, _) in pairwise(edges):
            assert right <= left + 0.5, (lang, path, width, edges)
        assert edges[-1][1] <= width - 20, (lang, width)
        assert row.bounding_box()["height"] <= 90, (lang, width)
        assert page.evaluate("document.documentElement.scrollWidth") == width, (
            lang,
            width,
        )
        targets = page.locator(".release-nav-group > summary, .release-github")
        for target in targets.all():
            box = target.bounding_box()
            assert box["width"] >= 44 and box["height"] >= 44, (lang, path, width, box)
        search = page.locator(".release-search-slot input").bounding_box()
        assert search["width"] >= 200, (lang, width)


def test_navigation_menu_on_phones_keeps_github_and_starts_with_search(release_page):
    page = release_page
    for width in (320, 390, 768, 1099):
        _nav_ready(page, "/?lang=fr-CH", width)
        page.evaluate(SEARCH_STAND_IN)
        assert page.locator(".release-menu-toggle").is_visible()
        assert page.locator(".release-github").is_visible()
        assert page.evaluate("document.documentElement.scrollWidth") == width
        page.locator(".release-menu-toggle").click()
        menu = page.locator("#release-menu")
        first_visible = menu.evaluate(
            "el => [...el.children].find(c => c.getClientRects().length).className"
        )
        assert first_visible == "release-search-slot", width
        assert "⌄" not in menu.inner_text()
        group = menu.locator(".release-nav-group").first
        summary = group.locator("summary")
        # A plus sign marks a closed group and a minus sign an open one.
        closed = summary.evaluate(
            "el => getComputedStyle(el, '::after').backgroundSize"
        )
        summary.click()
        assert group.evaluate("el => el.open")
        assert (
            summary.evaluate("el => getComputedStyle(el, '::after').backgroundSize")
            != closed
        )
        assert summary.bounding_box()["height"] >= 44
        for link in group.locator(".release-nav-panel a").all():
            assert link.is_visible() and link.bounding_box()["height"] >= 44
        assert page.evaluate("document.documentElement.scrollWidth") == width
        page.locator(".release-menu-toggle").click()


LINE_DURATION = """el => getComputedStyle(
    el.querySelector('.release-nav-line'), '::after'
).transitionDuration"""


def test_navigation_line_appears_without_motion_when_reduced(release_page):
    page = release_page
    page.emulate_media(reduced_motion="reduce")
    _nav_ready(page, "/?lang=de-CH")
    summary = page.locator(".release-nav-group summary").first
    assert summary.evaluate(LINE_DURATION) == "0s"
    page.emulate_media(reduced_motion="no-preference")
    page.reload()
    # The line grows from the left when motion is allowed.
    assert summary.evaluate(LINE_DURATION) != "0s"


@pytest.mark.parametrize("locale", ["de-CH", "fr-CH", "it-CH", "en"])
def test_desktop_header_labels_stay_on_one_line(release_page, locale):
    """French labels wrapped once the GitHub button took room; each must fit."""
    page = release_page
    for width in (1100, 1150, 1200, 1280, 1300, 1320, 1340, 1440, 1920):
        page.set_viewport_size({"width": width, "height": 800})
        assert page.goto(f"http://qa.test/?lang={locale}").status == 200
        page.evaluate(SEARCH_STAND_IN)
        # Compare with clientWidth: a classic scrollbar narrows the layout.
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        # The bar's items must also fit inside the container, not its padding.
        assert page.evaluate(
            "(r => r.scrollWidth <= r.clientWidth)"
            "(document.querySelector('.release-nav-row'))"
        )
        # Every label sits on one line (one line of 16 px text is about 26 px).
        heights = page.evaluate(
            """() => [...document.querySelectorAll(
                '.release-nav-group > summary .release-nav-line'
            )].filter(el => el.offsetParent)
              .map(el => Math.round(el.getBoundingClientRect().height))"""
        )
        assert heights and max(heights) <= 34, (width, heights)


@pytest.mark.parametrize("width", [320, 1280])
def test_search_results_fit_and_keep_large_targets(release_page, width):
    page = release_page
    page.set_viewport_size({"width": width, "height": 900})
    assert page.goto("http://qa.test/suche?q=Messpunkt").status == 200
    assert page.locator(".site-search-result").count() >= 1
    assert page.evaluate("document.documentElement.scrollWidth") == width
    for selector in (".site-search-title", ".site-search-page-form button"):
        for box in page.locator(selector).evaluate_all(
            "nodes => nodes.map(node => node.getBoundingClientRect().height)"
        ):
            assert box >= 44, selector
    for selector in (".site-search-meta", ".site-search-snippet", ".site-search-title"):
        color = page.locator(selector).first.evaluate(
            "el => getComputedStyle(el).color"
        )
        rgb = [int(part) for part in color[color.index("(") + 1 : -1].split(",")[:3]]
        hex_color = "#" + "".join(f"{part:02x}" for part in rgb)
        assert contrast(hex_color, "#f6f2ea") >= 4.5, selector
    mark = page.locator(".site-search-snippet mark").first
    assert mark.evaluate("el => getComputedStyle(el).fontWeight") == "700"


def test_empty_search_offers_large_example_links(release_page):
    page = release_page
    page.set_viewport_size({"width": 320, "height": 900})
    assert page.goto("http://qa.test/suche?q=qqqqq").status == 200
    assert page.evaluate("document.documentElement.scrollWidth") == 320
    heights = page.locator(
        ".site-search-examples a, .site-search-shortcuts a"
    ).evaluate_all("nodes => nodes.map(node => node.getBoundingClientRect().height)")
    assert heights and min(heights) >= 44


def test_highlighted_result_titles_keep_word_spacing(release_page):
    page = release_page
    assert page.goto("http://qa.test/suche?q=Rechnung").status == 200
    # Distance from the last letter before a highlight to the highlight: a
    # rendered space, not zero.
    gaps = page.locator(".site-search-title").evaluate_all(
        """titles => titles.flatMap(title => [...title.querySelectorAll('mark')].map(mark => {
            const before = mark.previousSibling;
            if (!before || before.nodeType !== 3 || !/\\S\\s+$/.test(before.data)) return null;
            const end = before.data.replace(/\\s+$/, '').length;
            const range = document.createRange();
            range.setStart(before, end - 1);
            range.setEnd(before, end);
            return mark.getBoundingClientRect().left - range.getBoundingClientRect().right;
        })).filter(gap => gap !== null)"""
    )
    assert gaps and min(gaps) > 2


@pytest.mark.parametrize("locale", ["de-CH", "fr-CH", "it-CH", "en"])
def test_navigation_search_field_keeps_its_width_on_desktop(release_page, locale):
    """The nav slot is not a flex container; without a set width the field
    collapsed to 120 px and clipped «Suchen» and «Rechercher»."""
    page = release_page
    for width in (1100, 1280, 1440):
        page.set_viewport_size({"width": width, "height": 800})
        assert page.goto(f"http://qa.test/?lang={locale}").status == 200
        field = page.locator(".release-nav #site-search-q")
        assert field.bounding_box()["width"] == 200
        assert page.evaluate(
            "(r => r.scrollWidth <= r.clientWidth)"
            "(document.querySelector('.release-nav-row'))"
        )
