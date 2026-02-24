"""Health check blueprint for OpenLEG."""
from flask import Blueprint, jsonify
import database as db

health_bp = Blueprint("health", __name__)


@health_bp.route("/health")
def health():
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return jsonify({"status": "healthy", "db": "connected"}), 200
    except Exception:
        return jsonify({"status": "degraded", "db": "disconnected"}), 503


@health_bp.route("/livez")
def livez():
    return "ok", 200
