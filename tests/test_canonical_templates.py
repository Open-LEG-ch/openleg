# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical public template routing."""

from flask import Flask, g
from jinja2 import DictLoader

import app as app_module


def test_tenant_context_cannot_override_the_tracked_template():
    flask_app = Flask(__name__)
    flask_app.config["SITE_URL"] = "https://openleg.ch"
    flask_app.jinja_loader = DictLoader(
        {
            "index.html": "canonical|{{ marker }}|{{ tenant.territory }}|{{ site_url }}|{{ ga4_id }}",
            "cities/zurich/index.html": "legacy tenant override",
        }
    )
    tenant = {"territory": "zurich", "ga4_id": ""}

    with flask_app.test_request_context("/"):
        g.tenant = tenant
        result = app_module.render_city_template("index.html", marker="value")

    assert result == "canonical|value|zurich|https://openleg.ch|"
