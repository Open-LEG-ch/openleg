# SPDX-License-Identifier: AGPL-3.0-or-later
"""Address geocoding must not wait on swisstopo forever."""

from unittest.mock import MagicMock, patch

import requests

import data_enricher


def test_geocoding_an_address_sets_a_timeout():
    response = MagicMock()
    response.json.return_value = {
        "results": [{"attrs": {"lat": 47.0, "lon": 8.0, "plz": 8000}}]
    }
    with patch("data_enricher.requests.get", return_value=response) as get:
        result = data_enricher.get_coordinates_from_address("Bahnhofstrasse 1 Zürich")

    assert result == (47.0, 8.0, 8000)
    assert get.call_args.kwargs.get("timeout") == 5


def test_a_geocoding_timeout_reports_no_coordinates():
    with patch("data_enricher.requests.get", side_effect=requests.Timeout("slow")):
        result = data_enricher.get_coordinates_from_address("Bahnhofstrasse 1 Zürich")

    assert result == (None, None, None)
