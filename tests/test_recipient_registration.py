# SPDX-License-Identifier: AGPL-3.0-or-later
"""Explicit signup language is validated before persistence."""

import pytest

from tests import test_registration_contract

app_module = test_registration_contract.app_module
registration = test_registration_contract.registration
valid_data = test_registration_contract.valid_data


@pytest.mark.parametrize("url", ["/api/register_anonymous", "/api/register_full"])
def test_signup_persists_selected_language(registration, url):
    client, db, _threads = registration
    response = client.post(url, json=valid_data(preferred_locale="fr-CH"))
    assert response.status_code == 200
    assert db.save_building.call_args.kwargs["preferred_locale"] == "fr_CH"


@pytest.mark.parametrize("selected", [None, "it_CH", "../../fr_CH"])
def test_invalid_explicit_signup_language_does_not_write(registration, selected):
    client, db, _threads = registration
    response = client.post(
        "/api/register_anonymous", json=valid_data(preferred_locale=selected)
    )
    assert response.status_code == 400
    db.save_building.assert_not_called()
