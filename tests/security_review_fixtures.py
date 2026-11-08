# SPDX-License-Identifier: AGPL-3.0-or-later
"""App fixtures with synthetic configuration for the security regression seams."""

import pytest

from tests.test_app_organic_routes import _disable_rate_limit_hooks
from tests.test_i18n import _synthetic_environment


def make_web(**overrides):
    with _synthetic_environment():
        import app

        return app.create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "synthetic-security-session-key-with-32-characters",
                "RATELIMIT_STORAGE_URI": "memory://",
                **overrides,
            },
            load_environment=False,
            check_database=False,
        )


@pytest.fixture
def app_module():
    with _synthetic_environment():
        import app

        web = make_web()
    hooks = _disable_rate_limit_hooks(web)
    app.web = web
    try:
        yield app
    finally:
        web.before_request_funcs[None] = hooks
