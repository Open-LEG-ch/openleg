# SPDX-License-Identifier: AGPL-3.0-or-later
"""Query-count contracts for stable public read paths (#288)."""

from unittest.mock import MagicMock

from tests.test_app_organic_routes import (  # noqa: F401
    full_app_module as organic_app_module,
)


def test_sitemap_reuses_rendered_xml_within_cache_window(
    organic_app_module,  # noqa: F811
    monkeypatch,
):
    values = {}
    load_ids = MagicMock(return_value=[261, 247])
    monkeypatch.setattr(
        organic_app_module.db, "get_all_municipality_profile_bfs_numbers", load_ids
    )
    monkeypatch.setattr(
        organic_app_module.cache_module, "cache_get", lambda key: values.get(key)
    )
    monkeypatch.setattr(
        organic_app_module.cache_module,
        "cache_set",
        lambda key, value, ttl: values.__setitem__(key, value),
    )
    client = organic_app_module.web.test_client()

    first = client.get("/sitemap.xml")
    second = client.get("/sitemap.xml")

    assert first.status_code == second.status_code == 200
    assert first.data == second.data
    assert load_ids.call_count == 1
    assert values
