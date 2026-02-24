"""Health check blueprint for OpenLEG."""
from flask import Blueprint, jsonify
import database as db

health_bp = Blueprint("health", __name__)


@health_bp.route("/health")
def health():
    status = {"status": "healthy"}

    # Check DB
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        status["db"] = "connected"
    except Exception:
        status["db"] = "disconnected"
        status["status"] = "degraded"

    # Check Redis
    try:
        import cache
        r = cache._get_redis()
        r.ping()
        status["redis"] = "connected"
    except Exception:
        status["redis"] = "disconnected"

    code = 200 if status["status"] == "healthy" else 503
    return jsonify(status), code


@health_bp.route("/livez")
def livez():
    return "ok", 200
