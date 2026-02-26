"""Visual smoke tests: headless browser checks for conversion partials.

Requires: pip install playwright && playwright install chromium
Run:  pytest tests/test_visual_smoke.py -v
"""
import os
import signal
import socket
import subprocess
import time
import pytest

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

pytestmark = pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright not installed")

PAGES = {
    "index": "/",
    "fuer-gemeinden": "/fuer-gemeinden",
    "fuer-bewohner": "/fuer-bewohner",
    "how-it-works": "/how-it-works",
    "pricing": "/pricing",
}


def _free_port():
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    port = _free_port()
    env = {**os.environ, "FLASK_RUN_PORT": str(port)}
    proc = subprocess.Popen(
        ["python", "app.py"],
        env=env,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wait for server
    for _ in range(30):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                break
        except OSError:
            time.sleep(0.5)
    else:
        proc.kill()
        pytest.skip("Flask server did not start")
    yield f"http://127.0.0.1:{port}"
    os.kill(proc.pid, signal.SIGTERM)
    proc.wait(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "screenshots")


class TestVisualSmoke:
    @pytest.mark.parametrize("name,path", PAGES.items())
    def test_page_loads_no_console_errors(self, server, browser, name, path):
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        page = browser.new_page()
        errors = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.goto(f"{server}{path}", wait_until="networkidle")
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, f"{name}.png"), full_page=True)
        page.close()
        assert not errors, f"JS errors on {path}: {errors}"

    @pytest.mark.parametrize("name,path", PAGES.items())
    def test_trust_bar_visible(self, server, browser, name, path):
        page = browser.new_page()
        page.goto(f"{server}{path}", wait_until="networkidle")
        bar = page.locator(".bg-white\\/80").first
        assert bar.is_visible(), f"Trust bar not visible on {path}"
        page.close()

    def test_savings_input_typeable(self, server, browser):
        page = browser.new_page()
        page.goto(f"{server}/", wait_until="networkidle")
        inp = page.locator("#sw-input").first
        inp.fill("Dietikon")
        assert inp.input_value() == "Dietikon"
        page.close()
