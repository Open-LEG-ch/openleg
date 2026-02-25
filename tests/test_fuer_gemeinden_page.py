import os
import pytest
from unittest.mock import patch, MagicMock


class TestFuerGemeindenPage:
    def test_page_renders(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://x:x@localhost/x", "REDIS_URL": "memory://"}):
            with patch("database.is_db_available", return_value=True), \
                 patch("database.init_db", return_value=True), \
                 patch("database._connection_pool", MagicMock()):
                try:
                    from app import app
                except Exception:
                    pytest.skip("App import requires live DB")

                client = app.test_client()
                hooks = list(app.before_request_funcs.get(None, []))
                app.before_request_funcs[None] = [
                    hook for hook in hooks
                    if not (
                        getattr(hook, "__module__", "").startswith("flask_limiter")
                        or getattr(hook, "__name__", "") == "_check_request_limit"
                    )
                ]
                try:
                    resp = client.get("/fuer-gemeinden")
                finally:
                    app.before_request_funcs[None] = hooks
                assert resp.status_code == 200
                html = resp.data.decode("utf-8", errors="ignore")
                assert "OpenLEG für Gemeinden" in html
                assert "Selbst betreiben" in html
                assert "Gehostet" in html
                assert "github.com/wgusta/openleg" in html
