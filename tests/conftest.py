"""Pytest fixtures and mock data for OpenLEG tests."""
import os
import sys
import pytest
from unittest.mock import MagicMock, patch

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock database before importing app
os.environ.setdefault('DATABASE_URL', '')
os.environ.setdefault('ADMIN_TOKEN', 'test-admin-token')
os.environ.setdefault('CRON_SECRET', 'test-cron-secret')


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
    with patch.dict('sys.modules', {'database': MagicMock()}):
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
    with patch('database.is_db_available', return_value=True), \
         patch('database.init_db', return_value=True), \
         patch('database.get_stats', return_value={'total_buildings': 0}), \
         patch('database.seed_default_tenant', return_value=True):
        # Import after mocking
        from api_public import public_api_bp
        from flask import Flask

        test_app = Flask(__name__, template_folder=os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'templates'
        ))
        test_app.config['TESTING'] = True
        test_app.register_blueprint(public_api_bp)
        yield test_app


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()
