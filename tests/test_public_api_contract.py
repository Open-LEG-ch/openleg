# SPDX-License-Identifier: AGPL-3.0-or-later
"""The committed public contract is served unchanged at the HTTP boundary."""

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.test_app_organic_routes import full_app_module as organic_app_module

full_app_module = organic_app_module

ROOT = Path(__file__).resolve().parents[1]


def test_public_contract_is_served_as_json(client):
    response = client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert response.get_json() == json.loads(
        (ROOT / "docs/openapi/public-v1.json").read_text()
    )
    assert response.get_json()["openapi"] == "3.1.0"


def test_public_routes_match_contract_in_both_directions(full_app_module):
    import re

    app = full_app_module.web
    spec = app.test_client().get("/api/v1/openapi.json").get_json()
    routes = {
        (method.lower(), re.sub(r"<(?:[^:<>]+:)?([^<>]+)>", r"{\1}", rule.rule))
        for rule in app.url_map.iter_rules()
        if rule.rule.startswith("/api/v1/") and rule.rule != "/api/v1/docs"
        for method in rule.methods - {"HEAD", "OPTIONS"}
    }
    documented = {
        (method, "/api/v1" + path)
        for path, operations in spec["paths"].items()
        for method in operations
        if method
        in {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
    }
    assert routes == documented, (
        f"Undocumented routes: {routes - documented}; "
        f"Stale operations: {documented - routes}"
    )


def test_every_operation_documents_response_schemas(client):
    spec = client.get("/api/v1/openapi.json").get_json()
    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            for status, response in operation["responses"].items():
                assert (
                    response.get("content", {})
                    .get("application/json", {})
                    .get("schema")
                ), (path, method, status)


def test_public_contract_is_valid_openapi(client):
    from openapi_spec_validator import validate

    validate(client.get("/api/v1/openapi.json").get_json())


def test_docs_load_served_contract(client):
    html = client.get("/api/v1/docs").get_data(as_text=True)
    assert '"/api/v1/openapi.json"' in html
    assert "loadApiReference(" in html
    assert "const spec =" not in html
    assert "openapi:" not in html


def test_release_image_packages_contract():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "COPY docs/openapi/ docs/openapi/" in dockerfile


def test_additional_public_responses(client, validate_public_api_responses):
    from unittest.mock import patch

    import data_enricher

    with (
        patch("homepage_view_model.db.get_stats", return_value={"total_buildings": 7}),
        patch("homepage_view_model.Ranking.load") as ranking,
    ):
        ranking.return_value.national.return_value = []
        assert client.get("/api/v1/site/home").get_json()["stats"] == {
            "registered_buildings": 7
        }

    with patch(
        "data_enricher.get_address_suggestions",
        return_value=[{"label": "Bundesplatz 1, Bern", "lat": 46.95, "lon": 7.44}],
    ):
        assert client.get("/api/v1/address/suggest?q=Bundesplatz").status_code == 200

    estimates, _ = data_enricher.get_mock_energy_profile_for_address("Bundesplatz 1")
    with patch(
        "data_enricher.get_energy_profile_for_address",
        return_value=(estimates, None),
    ):
        assert (
            client.get("/api/v1/address/profile?address=Bundesplatz").status_code == 200
        )
    assert client.get("/api/v1/address/profile").status_code == 400
    with (
        patch(
            "data_enricher.get_energy_profile_for_address", return_value=(None, None)
        ),
        patch(
            "data_enricher.get_mock_energy_profile_for_address",
            return_value=(None, None),
        ),
    ):
        assert client.get("/api/v1/address/profile?address=missing").status_code == 404

    assert client.post("/api/v1/leg/cluster", json={}).status_code == 400
    assert (
        client.post(
            "/api/v1/leg/cluster",
            json={"buildings": [{"lat": 47.37, "lon": 8.54}] * 301},
        ).status_code
        == 413
    )
    assert (
        client.post(
            "/api/v1/leg/cluster",
            json={
                "buildings": [
                    {
                        "building_id": str(i),
                        "lat": 47.37,
                        "lon": 8.54,
                        "annual_consumption_kwh": 4500,
                        "potential_pv_kwp": 5,
                    }
                    for i in range(2)
                ]
            },
        ).status_code
        == 200
    )
    with patch("ml_models.find_optimal_communities", side_effect=RuntimeError("test")):
        assert (
            client.post(
                "/api/v1/leg/cluster",
                json={
                    "buildings": [
                        {
                            "building_id": str(i),
                            "lat": 47.37,
                            "lon": 8.54,
                            "annual_consumption_kwh": 4500,
                            "potential_pv_kwp": 5,
                        }
                        for i in range(2)
                    ]
                },
            ).status_code
            == 500
        )


def test_french_docs_translate_every_displayed_contract_string(full_app_module):
    """New spec prose must have a translation and reach the rendered reference."""
    import gettext
    import subprocess

    spec = json.loads((ROOT / "docs/openapi/public-v1.json").read_text())
    displayed = {spec["info"]["title"], spec["info"]["description"]}
    for operations in spec["paths"].values():
        for operation in operations.values():
            displayed.add(operation["summary"])
            displayed.update(
                parameter["description"]
                for parameter in operation.get("parameters", [])
                if parameter.get("description")
            )
            displayed.update(
                response["description"] for response in operation["responses"].values()
            )

    response = full_app_module.web.test_client().get("/api/v1/docs?lang=fr-CH")
    assert response.status_code == 200
    assert response.headers["Content-Language"] == "fr-CH"
    rendered = subprocess.run(
        ["node", str(ROOT / "tests/js/render_api_reference.cjs")],
        input=json.dumps({"html": response.get_data(as_text=True), "spec": spec}),
        text=True,
        capture_output=True,
        check=True,
    )
    texts = json.loads(rendered.stdout)
    catalog = gettext.translation("messages", ROOT / "translations", ["fr_CH"])
    for source in sorted(displayed):
        translated = catalog.gettext(source)
        assert translated != source, f"Missing French translation: {source}"
        assert any(translated in text for text in texts), (
            f"French reference did not render {source!r} as {translated!r}"
        )


@pytest.mark.parametrize(
    ("path", "collection", "field", "value", "wire_value"),
    [
        ("/site/rankings", "rankings", "pv_score_pct", "77.00", "77.00"),
        ("/site/rankings", "rankings", "display_score", "77.00", "77.0"),
        ("/site/rankings", "rankings", "pv_untapped_kw", "230.50", "230.50"),
        ("/site/rankings/movers", "movers", "score_now", "77.0000", "77.0000"),
        ("/site/rankings/movers", "movers", "score_prev", "78.2500", "78.2500"),
        ("/site/rankings/movers", "movers", "delta", "-1.2500", "-1.2500"),
        ("/site/home", "best", "score", "77.00", "77.0"),
        ("/site/home", "needs_action", "score", "77.00", "77.0"),
    ],
)
def test_decimal_store_metrics_match_public_contract(
    client, validate_public_api_responses, path, collection, field, value, wire_value
):
    # Keep ranking/domain code and Flask serialization real; replace only storage.
    store_field = "pv_score_pct" if field in {"display_score", "score"} else field
    rows = [
        {
            "bfs_number": bfs,
            "name": f"Gemeinde {bfs}",
            "kanton": "ZH",
            "population": 1000,
            "pv_score_pct": 77,
            "pv_untapped_kw": 230,
            "year": 2025,
            "score_now": 77,
            "score_prev": 78.25,
            "delta": -1.25,
            store_field: Decimal(value),
        }
        for bfs in range(1, 7)
    ]
    with (
        patch("store.ranking.get_pv_profiles", return_value=rows),
        patch("store.ranking.get_pv_movers", return_value=rows),
        patch("homepage_view_model.db.get_stats", return_value={"total_buildings": 7}),
    ):
        response = client.get("/api/v1" + path)
    assert response.status_code == 200
    payload = response.get_json()
    if path == "/site/home":
        payload = payload["ranking"]
    assert payload[collection][0][field] == wire_value
