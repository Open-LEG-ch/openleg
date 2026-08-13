# SPDX-License-Identifier: AGPL-3.0-or-later
from unittest.mock import MagicMock, patch

import data_enricher


def test_get_address_suggestions_skips_malformed_and_recovers_plz_from_label():
    """Malformed entries must not crash the parser; a non-string label and a
    later valid result with an invalid attrs.plz and HTML in the label must be
    recovered from the label.
    """
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "results": [
            "not-a-dict-result",
            {"attrs": "not-a-dict-attrs"},
            {"attrs": {"label": 123}},
            {
                "attrs": {
                    "label": "<b>Seestrasse 12</b>, <i>8700</i> Küsnacht ZH",
                    "lat": 47.319,
                    "lon": 8.584,
                    "plz": "invalid",
                }
            },
        ]
    }

    with patch("data_enricher.requests.get", return_value=mock_response) as mock_get:
        suggestions = data_enricher.get_address_suggestions("Seestrasse")

    mock_get.assert_called_once()
    assert suggestions == [
        {
            "label": "Seestrasse 12, 8700 Küsnacht ZH",
            "lat": 47.319,
            "lon": 8.584,
            "plz": 8700,
        }
    ]
