# SPDX-License-Identifier: AGPL-3.0-or-later
"""Issue #218 contracts for the open-source and self-host pathway."""

import importlib
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALL_COMMAND = "curl -fsSL https://openleg.ch/install.sh | bash"
OPERATING_MODELS = ("Selbst betreiben", "Gehostet auf openleg.ch")
PATHWAY_ROUTES = ("/open-source", "/self-host", "/pricing")


@pytest.fixture
def full_app_module():
    with (
        patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql://x:x@localhost/x",
                "REDIS_URL": "memory://",
                "CRON_SECRET": "test-cron-secret",
                "APP_BASE_URL": "http://localhost:5003",
            },
        ),
        patch("database.is_db_available", return_value=True),
        patch("database._connection_pool", MagicMock()),
    ):
        import app as app_module

        app_module = importlib.reload(app_module)
        app_module.web = app_module.create_app(load_environment=False)
        app_module.db.get_stats = lambda **_kwargs: {"total_buildings": 0}
        yield app_module


def _html(client, route):
    response = client.get(route)
    assert response.status_code == 200
    return response.data.decode("utf-8")


def test_install_command_renders_verbatim_on_self_host_and_homepage(full_app_module):
    client = full_app_module.web.test_client()

    for route in ("/self-host", "/public-preview"):
        assert INSTALL_COMMAND in _html(client, route)


def test_install_endpoint_serves_script_unchanged(full_app_module):
    response = full_app_module.web.test_client().get("/install.sh")

    assert response.status_code == 200
    assert response.data == (ROOT / "scripts" / "install.sh").read_bytes()


@pytest.mark.parametrize("route", PATHWAY_ROUTES)
def test_pathway_routes_state_agpl_and_data_ownership(full_app_module, route):
    html = _html(full_app_module.web.test_client(), route)

    assert "AGPL-3.0-or-later" in html
    assert 'href="https://github.com/Open-LEG-ch/openleg/blob/main/LICENSE"' in html
    assert "Smart-Meter-Daten bleiben innerhalb jeder LEG" in html
    assert "nicht verkauft" in html
    assert "nicht für Dritte aggregiert" in html


@pytest.mark.parametrize("route", PATHWAY_ROUTES)
def test_pathway_routes_use_municipality_operating_model_vocabulary(
    full_app_module, route
):
    municipality_html = _html(full_app_module.web.test_client(), "/fuer-gemeinden")
    pathway_html = _html(full_app_module.web.test_client(), route)

    for model in OPERATING_MODELS:
        assert model in municipality_html
        assert model in pathway_html


def test_documented_install_paths_are_backed_by_repository_files():
    console = (ROOT / "templates" / "partials" / "install_console.html").read_text()
    readme = (ROOT / "README.md").read_text()

    assert INSTALL_COMMAND in readme
    assert "curl -fsSL https://openleg.ch/install.sh -o install-openleg.sh" in console
    assert "bash install-openleg.sh" in console
    assert "./scripts/openleg install" in console
    assert (ROOT / "scripts" / "install.sh").is_file()
    assert (ROOT / "scripts" / "openleg").is_file()
    assert (ROOT / "docker-compose.yml").is_file()
    assert "docker compose up -d" in readme


@pytest.mark.parametrize("route", PATHWAY_ROUTES)
def test_pathway_routes_return_200(full_app_module, route):
    assert full_app_module.web.test_client().get(route).status_code == 200


def test_open_source_gives_technical_users_concrete_entry_points(full_app_module):
    html = _html(full_app_module.web.test_client(), "/open-source")

    for entry_point in (
        "app.py",
        "store/",
        "billing_engine.py",
        "scripts/tdd_cycle.sh",
    ):
        assert entry_point in html
    assert "consumer_charge" in html
    assert "producer_credit" in html
    assert "Entwurf" in html


def test_readme_orients_english_and_german_technical_users():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "## English" in readme
    assert "## Deutsch" in readme
    assert "### Current billing boundary" in readme
    assert "### Aktuelle Abrechnungsgrenze" in readme
    assert "billing_engine.py" in readme
    assert "scripts/tdd_cycle.sh gate" in readme
