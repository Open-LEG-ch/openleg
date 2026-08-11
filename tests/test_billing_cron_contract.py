# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract: the billing cron reports only work it actually performs.

Billing period generation is implemented in ``billing_engine`` but not yet
wired to a scheduled run (see the deliberately out of scope list in #262).
Until it is, ``/api/cron/process-billing`` must say so instead of counting
active communities as if they had been billed.
"""

import importlib
import os
from unittest.mock import MagicMock, patch

import pytest

CRON_SECRET = "test-cron-secret"


def _load_app(env_overrides):
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": "postgresql://x:x@localhost/x",
            "REDIS_URL": "memory://",
            "APP_BASE_URL": "http://localhost:5003",
            **env_overrides,
        },
    ):
        with (
            patch("database.is_db_available", return_value=True),
            patch("database._connection_pool", MagicMock()),
        ):
            import app as app_module

            return importlib.reload(app_module)


@pytest.fixture
def app_with_cron_secret():
    yield _load_app({"CRON_SECRET": CRON_SECRET})


def _post_process_billing(module):
    return module.app.test_client().post(
        "/api/cron/process-billing",
        headers={"X-Cron-Secret": CRON_SECRET},
    )


class TestBillingCronHonesty:
    def test_reports_zero_processed_while_billing_runs_are_not_wired(
        self, app_with_cron_secret
    ):
        communities = [
            {"community_id": "c1"},
            {"community_id": "c2"},
            {"community_id": "c3"},
        ]
        with patch.object(
            app_with_cron_secret.db,
            "get_active_communities",
            return_value=communities,
        ):
            response = _post_process_billing(app_with_cron_secret)

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["communities"] == 3
        assert payload["processed"] == 0, (
            "the cron must not count communities as processed billing runs"
        )

    def test_states_that_billing_runs_are_not_activated(self, app_with_cron_secret):
        with patch.object(
            app_with_cron_secret.db, "get_active_communities", return_value=[]
        ):
            response = _post_process_billing(app_with_cron_secret)

        payload = response.get_json()
        assert payload["activated"] is False
        assert payload["status"] == "not_activated"
        assert payload["reason"], "the response explains why nothing ran"

    def test_does_not_loop_over_communities_to_fake_progress(self):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(project_root, "app.py")) as handle:
            content = handle.read()
        start = content.index('@app.route("/api/cron/process-billing"')
        end = content.index("@app.route", start + 1)
        handler = content[start:end]
        assert "processed += 1" not in handler
