# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whole sentences, real plurals and distinct unsubscribe actions."""

import re
from html import unescape

import pytest

from tests import test_i18n

app_module = test_i18n.app_module
isolated_environment = test_i18n.isolated_environment
web = test_i18n.web

with test_i18n._synthetic_environment():
    import municipality


__all__ = ["web"]


@pytest.mark.parametrize("count", [0, 1, 2])
def test_municipality_counts_use_french_plural_rules(web, monkeypatch, count):
    original = municipality.render_template

    def render(name, **context):
        context["stats"] = {"total_buildings": count, "registrations_today": count}
        context["interest_summary"] = dict.fromkeys(
            ["verified_total", "last_30_days", "has_solar", "address_problems"], count
        )
        return original(name, **context)

    monkeypatch.setattr(municipality, "render_template", render)
    response = web.test_client().get("/gemeinde/dashboard/demo?lang=fr")
    assert response.status_code == 200
    text = " ".join(re.sub("<[^>]*>", " ", unescape(response.text)).split())
    assert f"{count} {'ménage' if count < 2 else 'ménages'}" in text
    assert (
        f"{count} {'personne ayant confirmé' if count < 2 else 'personnes ayant confirmé'}"
        in text
    )
    assert f"{count} {'adresse restant' if count < 2 else 'adresses restant'}" in text
