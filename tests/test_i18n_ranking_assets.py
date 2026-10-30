# SPDX-License-Identifier: AGPL-3.0-or-later
"""Public ranking controls and embeddable SVGs retain their chosen language."""

import xml.etree.ElementTree as ET

import pytest

from tests import test_i18n

isolated_environment = test_i18n.isolated_environment
app_module = test_i18n.app_module
web = test_i18n.web

with test_i18n._synthetic_environment():
    import rangliste
    from ranking import Ranking


@pytest.mark.parametrize("path", ["/rangliste", "/rangliste/fortschritte"])
def test_ranking_filters_are_french_without_changing_machine_values(
    web, monkeypatch, path
):
    monkeypatch.setattr(Ranking, "load", lambda: Ranking([]))
    monkeypatch.setattr(Ranking, "movers", lambda *a, **kw: [])
    response = web.test_client().get(path + "?lang=fr-CH")
    assert response.status_code == 200
    assert "Toutes les tailles" in response.text
    assert "Toutes les densités" in response.text
    assert 'value="small"' in response.text
    assert 'value="very_high"' in response.text
    assert "Alle Grössen" not in response.text


@pytest.mark.parametrize("kind", ["badge", "og"])
@pytest.mark.parametrize("language", ["de-CH", "fr-CH"])
def test_svg_assets_are_localized_safe_xml(web, monkeypatch, kind, language):
    monkeypatch.setattr(
        rangliste.db,
        "get_municipality_profile",
        lambda bfs: {
            "bfs_number": bfs,
            "name": '<Commune "test">',
            "kanton": "VD",
            "pv_score_pct": 23.0,
        },
    )
    monkeypatch.setattr(Ranking, "load", lambda: Ranking([]))
    response = web.test_client().get(f"/rangliste/{kind}/1.svg?lang={language}")
    assert response.status_code == 200
    ET.fromstring(response.data)
    assert response.headers["Content-Language"] == language
    assert "Cookie" in response.vary
    assert (
        "Utilisation solaire" if language == "fr-CH" else "Solarnutzung"
    ) in response.text
    if language == "fr-CH":
        assert "Solarnutzung" not in response.text
        assert "Kanton" not in response.text


@pytest.mark.parametrize("count,noun", [(1, "commune"), (2, "communes")])
def test_ranking_count_uses_a_complete_french_plural(web, monkeypatch, count, noun):
    profiles = [
        {
            "bfs_number": index + 1,
            "name": f"Synthetic {index}",
            "kanton": "VD",
            "population": 1000,
            "pv_score_pct": 10.0,
            "pv_untapped_kw": 12,
        }
        for index in range(count)
    ]
    monkeypatch.setattr(Ranking, "load", lambda: Ranking(profiles))
    response = web.test_client().get("/rangliste?lang=fr-CH")
    assert response.status_code == 200
    assert f"{count} {noun} dans ce groupe" in response.text
    if count == 1:
        assert "1 communes" not in response.text


def test_og_card_preserves_profile_energy_potential(web, monkeypatch):
    import pv_badge

    captured = {}

    def render(name, canton, score, rank, untapped_kw):
        captured["untapped_kw"] = untapped_kw
        return "synthetic-svg"

    monkeypatch.setattr(pv_badge, "og_card_svg", render)
    profile = {
        "bfs_number": 1,
        "name": "Synthetic",
        "kanton": "VD",
        "pv_score_pct": 23,
        "pv_untapped_kw": 1234,
    }
    assert Ranking([profile]).og_card_svg(1) == "synthetic-svg"
    assert captured["untapped_kw"] == 1234
