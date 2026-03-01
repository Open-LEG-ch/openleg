"""Tests for demo provisioning endpoints under /gemeinde/demo/*."""
import os
import pytest
from flask import Flask

import municipality as municipality_module


def _template_dir():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "templates")


@pytest.fixture
def demo_client(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("DEMO_SUBDOMAIN", "newbaden")
    monkeypatch.setenv("DEMO_ENV", "staging")

    app = Flask(__name__, template_folder=_template_dir())
    app.config["TESTING"] = True
    app.register_blueprint(municipality_module.municipality_bp)
    return app.test_client()


def _mock_demo_storage(monkeypatch):
    state = {"tenant_exists": False}

    def fake_get_tenant(_territory):
        if not state["tenant_exists"]:
            return None
        return {"territory": "newbaden", "active": True}

    def fake_upsert(_territory, _config):
        state["tenant_exists"] = True
        return True

    monkeypatch.setattr(municipality_module.db, "get_tenant_by_territory", fake_get_tenant)
    monkeypatch.setattr(municipality_module.db, "upsert_tenant", fake_upsert)
    monkeypatch.setattr(municipality_module.db, "save_municipality", lambda **_: 101)
    monkeypatch.setattr(municipality_module.db, "update_municipality_status", lambda *_, **__: True)
    monkeypatch.setattr(municipality_module.db, "save_municipality_profile", lambda *_: True)
    monkeypatch.setattr(municipality_module.db, "track_event", lambda *_, **__: True)
    monkeypatch.setattr(municipality_module.tenant_module, "invalidate_cache", lambda *_: None)


def test_build_demo_tenant_config_contains_expected_fields(monkeypatch):
    monkeypatch.setenv("DEMO_SUBDOMAIN", "newbaden")
    config = municipality_module.build_demo_tenant_config({
        "municipality_name": "Newbaden",
        "contact_name": "Max Muster",
        "contact_email": "verwaltung@newbaden.ch",
        "kanton": "Aargau",
        "kanton_code": "AG",
        "dso_name": "Regionalwerke Baden",
        "population": 22000,
    })
    assert config["territory"] == "newbaden"
    assert config["city_name"] == "Newbaden"
    assert config["kanton_code"] == "AG"
    assert config["platform_name"] == "Newbaden OpenLEG"


def test_demo_provision_is_idempotent(demo_client, monkeypatch):
    _mock_demo_storage(monkeypatch)

    payload = {
        "municipality_name": "Newbaden",
        "contact_name": "Max Muster",
        "contact_email": "verwaltung@newbaden.ch",
        "kanton": "Aargau",
        "kanton_code": "AG",
        "population": 22000,
        "dso_name": "Regionalwerke Baden",
    }

    first = demo_client.post("/gemeinde/demo/provision", json=payload)
    assert first.status_code == 200
    first_data = first.get_json()
    assert first_data["success"] is True
    assert first_data["already_exists"] is False
    assert first_data["demo_url"] == "https://newbaden.openleg.ch"

    second = demo_client.post("/gemeinde/demo/provision", json=payload)
    assert second.status_code == 200
    second_data = second.get_json()
    assert second_data["success"] is True
    assert second_data["already_exists"] is True

    status = demo_client.get("/gemeinde/demo/status")
    assert status.status_code == 200
    status_data = status.get_json()
    assert status_data["enabled"] is True
    assert status_data["ready"] is True
    assert status_data["tenant_exists"] is True


def test_demo_provision_validation_error(demo_client, monkeypatch):
    _mock_demo_storage(monkeypatch)

    response = demo_client.post("/gemeinde/demo/provision", json={
        "municipality_name": "Newbaden",
        "contact_name": "Max Muster",
    })
    assert response.status_code == 400
    data = response.get_json()
    assert "error" in data


def test_onboarding_renders_demo_form_when_enabled(demo_client):
    response = demo_client.get("/gemeinde/onboarding")
    assert response.status_code == 200
    html = response.data.decode("utf-8", errors="ignore")
    assert "Demo-Instanz erstellen" in html
    assert "id=\"demo-form\"" in html


def test_demo_status_disabled(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "false")
    app = Flask(__name__, template_folder=_template_dir())
    app.config["TESTING"] = True
    app.register_blueprint(municipality_module.municipality_bp)
    client = app.test_client()

    response = client.get("/gemeinde/demo/status")
    assert response.status_code == 503
    data = response.get_json()
    assert data["enabled"] is False


def test_onboarding_shows_disabled_notice_when_demo_off(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "false")
    app = Flask(__name__, template_folder=_template_dir())
    app.config["TESTING"] = True
    app.register_blueprint(municipality_module.municipality_bp)
    client = app.test_client()

    response = client.get("/gemeinde/onboarding")
    assert response.status_code == 200
    html = response.data.decode("utf-8", errors="ignore")
    assert "Demo-Modus ist deaktiviert" in html
