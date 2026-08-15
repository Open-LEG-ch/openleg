# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guard against re-introduction of unauthenticated location endpoints (#319)."""

from tests.test_app_organic_routes import (  # noqa: F401
    full_app_module as organic_app_module,
)

_FORBIDDEN_PATHS = ("/api/get_all_clusters", "/api/get_all_buildings")


def test_location_endpoints_are_not_registered(
    organic_app_module,  # noqa: F811
):
    rules = {rule.rule for rule in organic_app_module.web.url_map.iter_rules()}
    for path in _FORBIDDEN_PATHS:
        assert path not in rules
