# SPDX-License-Identifier: AGPL-3.0-or-later
"""French public API messages and confirmation mail through actual route seams."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests import test_i18n

isolated_environment = test_i18n.isolated_environment
app_module = test_i18n.app_module
web = test_i18n.web

# Imported route seams also read configuration on a cold Python process.
with test_i18n._synthetic_environment():
    import app
    import meter_data
    import registration
    import security_utils
    import tenant


@pytest.fixture
def public_client(web, monkeypatch):
    monkeypatch.setattr(
        tenant,
        "get_tenant_config",
        lambda *args, **kwargs: {
            **tenant.DEFAULT_TENANT,
            "territory": "vully",
            "city_name": "Vully-les-Lacs",
            "platform_name": "BIVIS",
        },
    )
    monkeypatch.setattr(app, "send_email", MagicMock())
    return web.test_client()


@pytest.mark.parametrize(
    "path", ["check_potential", "register_anonymous", "register_interest"]
)
def test_empty_public_requests_return_french_error(public_client, path):
    response = public_client.post("/api/" + path + "?lang=fr-CH", json={})
    assert response.status_code == 400
    assert response.json == {"error": "Aucune donnée reçue."}


def test_registration_validation_keeps_domain_german_but_presents_french(public_client):
    response = public_client.post(
        "/api/register_anonymous?lang=fr-CH", json={"email": "not-an-email"}
    )
    assert response.status_code == 400
    assert response.json == {"error": "Adresse e-mail non valide."}
    assert (
        security_utils.validate_email_address("not-an-email")[2]
        == "Ungültige E-Mail-Adresse."
    )
    with pytest.raises(registration.RegistrationError) as error:
        registration.parse_roles(["unknown"])
    assert error.value.message == "Bitte wählen Sie gültige Rollen aus."


def test_check_potential_translates_error_but_preserves_reason(
    public_client, monkeypatch
):
    monkeypatch.setattr(
        app.data_enricher,
        "resolve_address_profile",
        lambda address: SimpleNamespace(
            source="fallback", live_status="upstream_unavailable"
        ),
    )
    response = public_client.post(
        "/api/check_potential?lang=fr-CH",
        json={"address": "Route du Lac 1, 1586 Vallamand"},
    )
    assert response.status_code == 422
    assert response.json == {
        "error": "L’adresse n’a pas pu être vérifiée avec certitude.",
        "can_register_interest": True,
        "reason": "upstream_unavailable",
    }


def test_meter_upload_validation_is_french(public_client, monkeypatch):
    invalid = public_client.post("/api/meter-data/upload?lang=fr-CH", json={"tier": 8})
    assert invalid.status_code == 400
    assert invalid.json == {"error": "tier doit valoir 1, 2 ou 3."}
    monkeypatch.setattr(app.db, "get_building", lambda *args: {"building_id": "one"})
    monkeypatch.setattr(app.db, "save_data_consent", MagicMock())
    response = public_client.post(
        "/api/meter-data/upload?lang=fr-CH",
        json={"building_id": "one", "csv_content": "not-a-meter-file"},
    )
    assert response.status_code == 200
    assert response.json["success"] is False
    assert response.json["errors"] == [
        "Aucune mesure trouvée dans le fichier. Veuillez utiliser l’export CSV d’EKZ."
    ]


def test_meter_parser_keeps_named_values_and_german_default(web):
    content = "Timestamp;Consumption (kWh)\nbad timestamp;1\n"
    with web.test_request_context("/?lang=fr-CH"):
        readings, errors = meter_data.parse_ekz_csv(content)
        assert readings == []
        assert errors[0] == "Ligne 2 : horodatage non valide « bad timestamp »"
    assert (
        meter_data.parse_ekz_csv(content)[1][0]
        == "Zeile 2: Ungültiger Zeitstempel 'bad timestamp'"
    )
