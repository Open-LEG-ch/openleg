# SPDX-License-Identifier: AGPL-3.0-or-later
"""Refresh the public demo GIFs and stills from the repository root.

In a development environment with the app requirements and Pillow installed,
install Playwright and run `python -m playwright install chromium`. Then run
`python -m scripts.capture_product_media`. The capture uses the checked-in
styles and translations and serves requests through the Flask test client.
The checked-in media were captured with Playwright 1.65.0 and Pillow 13.0.0.
"""

import os
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode, urlsplit

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "static" / "images" / "product"
ORIGIN = "http://localhost"
DEMO_ROUTES = {
    "resident-dashboard": "/dashboard/demo",
    "municipality-dashboard": "/gemeinde/dashboard/demo",
    "leg-formation": "/leg/dashboard/demo",
}
VIEWPORT = {"width": 1120, "height": 630}
OUTPUT_SIZE = (960, 540)
FRAME_DURATIONS_MS = (1100, 1100, 1100, 400)


def serve_demo_request(route, client):
    """Serve demo pages, their same-origin assets and the local calculator."""
    request = route.request
    url = urlsplit(request.url)
    allowed_get = request.method == "GET" and (
        url.path in DEMO_ROUTES.values()
        or url.path.startswith("/static/")
        or url.path == "/favicon.ico"
    )
    # The calculator only evaluates the numbers prefilled by the resident demo.
    calculate = (
        request.method == "POST"
        and url.path == "/api/calculate_savings"
        and urlsplit(request.headers.get("referer", "")).path
        == DEMO_ROUTES["resident-dashboard"]
    )
    if f"{url.scheme}://{url.netloc}" != ORIGIN or not (allowed_get or calculate):
        route.abort()
        return

    response = client.open(
        url.path,
        method=request.method,
        query_string=url.query,
        data=request.post_data_buffer,
        content_type=request.headers.get("content-type"),
    )
    route.fulfill(
        status=response.status_code,
        headers=dict(response.headers),
        body=response.get_data(),
    )


def open_demo(browser_page, name, locale, **query):
    params = urlencode(dict(lang=locale, **query))
    response = browser_page.goto(f"{ORIGIN}{DEMO_ROUTES[name]}?{params}")
    if (
        response is None
        or not response.ok
        or browser_page.locator("html").get_attribute("lang") != locale
        or not browser_page.locator(".product-shell h1").is_visible()
    ):
        raise RuntimeError(f"Could not render {name} in {locale}")
    # Chromium's native select border can vary by a few pixels between captures.
    browser_page.add_style_tag(
        content="""
            *, *::before, *::after { transition: none !important; }
            #has-solar {
                appearance: none;
                background-image:
                    linear-gradient(45deg, transparent 50%, currentColor 50%),
                    linear-gradient(135deg, currentColor 50%, transparent 50%);
                background-position: right 10px center, right 6px center;
                background-repeat: no-repeat;
                background-size: 4px 4px;
            }
        """
    )


def screenshot(page, selector=None, *, top=16):
    if selector:
        page.locator(selector).evaluate(
            """(element, top) => {
                element.scrollIntoView({block: 'start', behavior: 'instant'});
                window.scrollBy({top: -top, behavior: 'instant'});
            }""",
            top,
        )
    # Let Chromium paint the final layout after scrolling or changing a form.
    page.evaluate(
        """async () => {
            await document.fonts.ready;
            await new Promise(resolve => requestAnimationFrame(
                () => requestAnimationFrame(resolve)
            ));
        }"""
    )
    with Image.open(BytesIO(page.screenshot(animations="disabled"))) as image:
        return image.convert("RGB").resize(OUTPUT_SIZE, Image.Resampling.LANCZOS)


def capture_flow(page, name, locale):
    open_demo(page, name, locale)
    opening = screenshot(page)
    if name == "resident-dashboard":
        details = screenshot(page, 'section[aria-labelledby="checks-title"]')
        page.locator("#calc-btn").click()
        page.locator("#savings-result").wait_for()
        page.mouse.move(0, 0)
        action = screenshot(page, "#savings-result", top=300)
    elif name == "municipality-dashboard":
        details = screenshot(page, 'section[aria-labelledby="interest-summary-title"]')
        # Leave space to align the last row without slicing the metrics above it.
        page.locator("main").evaluate("el => { el.style.paddingBottom = '8rem'; }")
        action = screenshot(page, 'section[aria-labelledby="steps-title"]')
    else:
        details = screenshot(
            page, "#main-content [data-leg-workspace] section:nth-of-type(2)", top=32
        )
        open_demo(page, name, locale, page="members")
        action = screenshot(page)
    return opening, details, action, opening


def encode(frames):
    animation = BytesIO()
    first, *rest = (frame.quantize(colors=256) for frame in frames)
    first.save(
        animation,
        format="GIF",
        save_all=True,
        append_images=rest,
        duration=FRAME_DURATIONS_MS,
    )
    with Image.open(animation) as gif:
        if gif.n_frames != len(frames) or animation.getbuffer().nbytes >= 5_000_000:
            raise RuntimeError("GIF must contain four frames and stay under 5 MB")

    still = BytesIO()
    frames[0].save(still, format="WEBP", quality=80, method=6)
    return animation.getvalue(), still.getvalue()


def capture_all(browser):
    # Isolate both import-time settings and requests from host credentials.
    with patch.dict(os.environ, {"DATABASE_URL": "", "REDIS_URL": ""}, clear=True):
        from app import create_app

        app = create_app(
            {
                "TESTING": True,
                "SITE_RELEASE_ENABLED": True,
                "RATELIMIT_STORAGE_URI": "memory://",
                "APP_BASE_URL": ORIGIN,
            },
            load_environment=False,
            check_database=False,
        )
        client = app.test_client(use_cookies=False)
        outputs = {}
        for locale, directory in (("de-CH", ASSET_DIR), ("fr-CH", ASSET_DIR / "fr-CH")):
            with browser.new_context(
                viewport=VIEWPORT, locale=locale, service_workers="block"
            ) as context:
                page = context.new_page()
                page.route("**/*", lambda route: serve_demo_request(route, client))
                for name in DEMO_ROUTES:
                    animation, still = encode(capture_flow(page, name, locale))
                    outputs[directory / f"{name}.gif"] = animation
                    outputs[directory / f"{name}-static.webp"] = still
        return outputs


def main():
    """Capture both locales before replacing the checked-in media."""
    with sync_playwright() as driver:
        # Use software rendering to keep native form-control edges stable.
        browser = driver.chromium.launch(
            args=[
                "--disable-gpu",
                "--disable-lcd-text",
                "--disable-font-subpixel-positioning",
            ]
        )
        try:
            outputs = capture_all(browser)
        finally:
            browser.close()
    for path, data in outputs.items():
        path.write_bytes(data)
        print(f"{path.relative_to(ROOT)} ({len(data):,} bytes)")


if __name__ == "__main__":
    main()
