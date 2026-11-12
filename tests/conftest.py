# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pytest fixtures and mock data for OpenLEG tests."""

import os
import re
import sys
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock database before importing app
os.environ.setdefault("DATABASE_URL", "")
os.environ.setdefault("ADMIN_TOKEN", "test-admin-token")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")


# === Test sharding ===


def pytest_addoption(parser):
    parser.addoption(
        "--shard",
        metavar="INDEX/COUNT",
        help="Run every COUNT-th selected test, starting with test INDEX (1-based).",
    )


_SHARD = pytest.StashKey[tuple[int, int]]()


def pytest_configure(config):
    value = config.getoption("--shard")
    if value is None:
        return
    match = re.fullmatch(r"([0-9]+)/([0-9]+)", value)
    if match is None:
        raise pytest.UsageError(f"--shard expects INDEX/COUNT, not {value!r}")
    index, count = int(match[1]), int(match[2])
    if not 1 <= index <= count:
        raise pytest.UsageError(f"--shard needs 1 <= INDEX <= COUNT, not {value!r}")
    config.stash[_SHARD] = index, count


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    """Keep one round-robin slice so slow neighbouring tests spread across shards."""
    if _SHARD not in config.stash:
        return
    index, count = config.stash[_SHARD]
    selected = items[index - 1 :: count]
    kept = {id(item) for item in selected}
    config.hook.pytest_deselected(
        items=[item for item in items if id(item) not in kept]
    )
    items[:] = selected


# === Mock Data ===

MOCK_ELCOM_TARIFFS = [
    {
        "bfs_number": 261,
        "year": 2026,
        "operator_name": "EKZ",
        "category": "H4",
        "total_rp_kwh": 27.5,
        "energy_rp_kwh": 12.0,
        "grid_rp_kwh": 9.5,
        "municipality_fee_rp_kwh": 3.0,
        "kev_rp_kwh": 3.0,
    },
    {
        "bfs_number": 261,
        "year": 2026,
        "operator_name": "EKZ",
        "category": "H1",
        "total_rp_kwh": 32.0,
        "energy_rp_kwh": 14.0,
        "grid_rp_kwh": 11.0,
        "municipality_fee_rp_kwh": 4.0,
        "kev_rp_kwh": 3.0,
    },
]

MOCK_MUNICIPALITY_PROFILE = {
    "bfs_number": 261,
    "name": "Dietikon",
    "kanton": "ZH",
    "population": 29000,
    "solar_potential_pct": 45.0,
    "solar_installed_kwp": 15000.0,
    "ev_share_pct": 12.0,
    "renewable_heating_pct": 35.0,
    "electricity_consumption_mwh": 180000.0,
    "renewable_production_mwh": 25000.0,
    "leg_value_gap_chf": 171.0,
    "energy_transition_score": 42.5,
    "data_sources": {"elcom": True, "energie_reporter": True},
}

MOCK_SONNENDACH = {
    "bfs_number": 261,
    "total_roof_area_m2": 500000.0,
    "suitable_roof_area_m2": 250000.0,
    "potential_kwh_year": 200000000.0,
    "potential_kwp": 180000.0,
    "utilization_pct": 8.3,
}

MOCK_PROFILES_LIST = [
    MOCK_MUNICIPALITY_PROFILE,
    {
        "bfs_number": 247,
        "name": "Schlieren",
        "kanton": "ZH",
        "population": 20000,
        "solar_potential_pct": 40.0,
        "solar_installed_kwp": 8000.0,
        "ev_share_pct": 10.0,
        "renewable_heating_pct": 30.0,
        "electricity_consumption_mwh": 120000.0,
        "renewable_production_mwh": 15000.0,
        "leg_value_gap_chf": 155.0,
        "energy_transition_score": 38.0,
        "data_sources": {},
    },
]


# === Fixtures ===


@pytest.fixture
def mock_db():
    """Mock database module."""
    with patch.dict("sys.modules", {"database": MagicMock()}):
        import database as db

        db.is_db_available = MagicMock(return_value=True)
        db.get_elcom_tariffs = MagicMock(return_value=MOCK_ELCOM_TARIFFS)
        db.save_elcom_tariffs = MagicMock(return_value=2)
        db.get_municipality_profile = MagicMock(return_value=MOCK_MUNICIPALITY_PROFILE)
        db.get_all_municipality_profiles = MagicMock(return_value=MOCK_PROFILES_LIST)
        db.save_municipality_profile = MagicMock(return_value=True)
        db.get_sonnendach_municipal = MagicMock(return_value=MOCK_SONNENDACH)
        db.save_sonnendach_municipal = MagicMock(return_value=True)
        yield db


@pytest.fixture
def app(mock_db):
    """Flask test app with mocked dependencies."""
    with (
        patch("database.is_db_available", return_value=True),
        patch("database.init_db", return_value=True),
        patch("database.get_stats", return_value={"total_buildings": 0}),
        patch("database.seed_default_tenant", return_value=True),
    ):
        # Import after mocking
        from flask import Flask

        from api_public import public_api_bp
        from health import health_bp
        from i18n import init_app as init_i18n

        test_app = Flask(
            __name__,
            template_folder=os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"
            ),
        )
        test_app.config.update(TESTING=True, SECRET_KEY="i18n-test-session")
        init_i18n(test_app)
        test_app.register_blueprint(public_api_bp)
        test_app.register_blueprint(health_bp)
        yield test_app


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture
def validate_public_api_responses(app):
    """Validate actual HTTP responses in the existing public API scenarios."""
    import json
    import re
    from pathlib import Path

    from flask import request
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    spec = json.loads(
        (
            Path(__file__).resolve().parents[1] / "docs/openapi/public-v1.json"
        ).read_text()
    )
    registry = Registry().with_resource(
        "urn:openleg:public-v1",
        Resource.from_contents(
            {"$schema": "https://json-schema.org/draft/2020-12/schema", **spec}
        ),
    )

    @app.after_request
    def validate_response(response):
        if request.url_rule is None or request.url_rule.rule == "/api/v1/docs":
            return response
        path = re.sub(
            r"<(?:[^:<>]+:)?([^<>]+)>", r"{\1}", request.url_rule.rule
        ).removeprefix("/api/v1")
        assert response.is_json
        # Keep local component references anchored to the complete document.
        validator = Draft202012Validator(
            {
                "$ref": "urn:openleg:public-v1#/paths/"
                + path.replace("~", "~0").replace("/", "~1")
                + "/"
                + request.method.lower()
                + "/responses/"
                + str(response.status_code)
                + "/content/application~1json/schema"
            },
            registry=registry,
        )
        validator.validate(response.get_json())
        return response
