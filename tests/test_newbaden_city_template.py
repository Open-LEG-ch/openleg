"""Integration check: host-based tenant routing renders newbaden city override."""
import os
from unittest.mock import patch, MagicMock


def _disable_limiter_hooks(app):
    hooks = list(app.before_request_funcs.get(None, []))
    app.before_request_funcs[None] = [
        hook for hook in hooks
        if not (
            getattr(hook, "__module__", "").startswith("flask_limiter")
            or getattr(hook, "__name__", "") == "_check_request_limit"
        )
    ]
    return hooks


def test_newbaden_host_renders_city_specific_index():
    env = {
        "DATABASE_URL": "postgresql://x:x@localhost/x",
        "REDIS_URL": "memory://",
        "DEMO_MODE": "true",
        "DEMO_SUBDOMAIN": "newbaden",
    }
    with patch.dict(os.environ, env):
        with patch("database.is_db_available", return_value=True), \
             patch("database._connection_pool", MagicMock()), \
             patch("database.get_stats", return_value={"total_buildings": 0}), \
             patch("database.get_building_by_referral_code", return_value=None), \
             patch("tenant.get_tenant_config", return_value={
                 "territory": "newbaden",
                 "city_name": "Newbaden",
                 "kanton": "Aargau",
                 "kanton_code": "AG",
                 "platform_name": "Newbaden OpenLEG",
                 "brand_prefix": "Newbaden",
                 "utility_name": "Regionalwerke Baden",
                 "primary_color": "#0f766e",
                 "secondary_color": "#d97706",
                 "contact_email": "verwaltung@newbaden.ch",
                 "dso_contact": "Regionalwerke Baden",
                 "active": True,
             }):
            from app import app

            client = app.test_client()
            hooks = _disable_limiter_hooks(app)
            try:
                response = client.get("/", headers={"Host": "newbaden.openleg.ch"})
            finally:
                app.before_request_funcs[None] = hooks

            assert response.status_code == 200
            html = response.data.decode("utf-8", errors="ignore")
            assert "Staging Demo" in html
            assert "kommunale OpenLEG Instanz" in html
            assert "newbaden.openleg.ch" in html
