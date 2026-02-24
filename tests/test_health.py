"""Health endpoint tests."""
import os
import sys
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def health_app():
    """Minimal Flask app with health blueprint."""
    from flask import Flask
    from health import health_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(health_bp)
    return app


@pytest.fixture
def health_client(health_app):
    return health_app.test_client()


class TestHealthEndpoint:

    def test_health_ok(self, health_client):
        with patch("health.db") as mock_db:
            mock_conn = MagicMock()
            mock_db.get_connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db.get_connection.return_value.__exit__ = MagicMock(return_value=False)
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

            resp = health_client.get("/health")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "healthy"
            assert data["db"] == "connected"

    def test_health_db_down(self, health_client):
        with patch("health.db") as mock_db:
            mock_db.get_connection.side_effect = Exception("connection refused")

            resp = health_client.get("/health")
            assert resp.status_code == 503
            data = resp.get_json()
            assert data["status"] == "degraded"
            assert data["db"] == "disconnected"

    def test_livez(self, health_client):
        resp = health_client.get("/livez")
        assert resp.status_code == 200
        assert resp.data == b"ok"
