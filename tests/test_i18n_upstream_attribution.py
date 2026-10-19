# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keep current upstream data attribution when translating public templates."""

import pytest

from tests import test_i18n_public

public_language_app = test_i18n_public.public_language_app


@pytest.mark.parametrize("page", ["index", "gemeinde/verzeichnis"])
@pytest.mark.parametrize("language", ["de-CH", "fr-CH"])
def test_current_municipal_feed_attribution_is_localized(
    public_language_app, page, language
):
    body = test_i18n_public.render_public(
        public_language_app,
        page,
        language=language,
        profiles=[
            {
                "name": "Vully-les-Lacs",
                "bfs_number": 1,
                "kanton": "VD",
                "population": 1,
                "interest_label": "0",
                "display_score": None,
                "pv_score_pct": None,
            }
        ],
        canton_options=[("VD", "Waadt")],
        query="",
        interest_available=True,
        plant_match_rate=None,
        ranking_best=[
            {
                "bfs_number": 1,
                "rank": 1,
                "name": "Vully-les-Lacs",
                "kanton": "VD",
                "score": 20,
            }
        ],
        ranking_worst=[
            {
                "bfs_number": 1,
                "rank": 1,
                "name": "Vully-les-Lacs",
                "kanton": "VD",
                "score": 20,
            }
        ],
    )
    assert 'href="https://www.energiereporter.ch/"' in body
    if language == "fr-CH":
        assert "Energie Reporter (geoimpact et SuisseEnergie)" in body
        assert "geoimpact und EnergieSchweiz" not in body
    else:
        assert "Energie Reporter (geoimpact und EnergieSchweiz)" in body
