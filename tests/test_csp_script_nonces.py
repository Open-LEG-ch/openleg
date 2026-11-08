# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rendered scripts use the response nonce on public and private pages."""

import re
from html.parser import HTMLParser

import pytest

from tests import security_review_fixtures
from tests.test_dashboard_access_routes import _set_session

app_module = security_review_fixtures.app_module


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.handlers = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.scripts.append(dict(attrs))
        self.handlers.extend(name for name, _ in attrs if name.startswith("on"))


@pytest.mark.parametrize(
    "path,private",
    [
        ("/", False),
        ("/dashboard/demo", False),
        ("/leg/dashboard/demo", False),
        ("/leg-kalkulator", False),
        ("/gemeinde/onboarding", False),
        ("/utility/login", False),
        ("/meter-upload", True),
    ],
)
def test_each_rendered_script_has_the_response_nonce(app_module, path, private):
    client = app_module.web.test_client()
    if private:
        _set_session(client)
    previous = None
    for _ in range(2):
        response = client.get(path)
        assert response.status_code == 200
        csp = response.headers["Content-Security-Policy"]
        scripts_policy = next(
            piece for piece in csp.split(";") if piece.strip().startswith("script-src ")
        )
        assert "'unsafe-inline'" not in scripts_policy
        match = re.search(r"'nonce-([^']+)'", scripts_policy)
        assert match is not None
        nonce = match.group(1)
        assert nonce != previous
        previous = nonce
        parsed = Scripts()
        parsed.feed(response.text)
        assert parsed.scripts
        assert parsed.handlers == []
        assert all(
            script.get("nonce") == nonce
            for script in parsed.scripts
            if script.get("type") not in {"application/json", "application/ld+json"}
        )
