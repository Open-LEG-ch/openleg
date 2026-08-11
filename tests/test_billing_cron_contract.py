# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract: the billing cron reports only work it actually performs.

Billing period generation is implemented in ``billing_engine`` but not yet
wired to a scheduled run (see the deliberately out of scope list in #262).
Until it is, ``/api/cron/process-billing`` must say so instead of counting
active communities as if they had been billed.
"""

from unittest.mock import patch

import pytest

import app as app_module
import database

CRON_SECRET = "test-cron-secret"


@pytest.fixture
def app_with_cron_secret():
    yield app_module.create_app(
        {
            "TESTING": True,
            "RATELIMIT_STORAGE_URI": "memory://",
            "APP_BASE_URL": "http://localhost:5003",
            "CRON_SECRET": CRON_SECRET,
        },
        load_environment=False,
        check_database=False,
    )


def _post_process_billing(application):
    return application.test_client().post(
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
        with patch.object(database, "get_active_communities", return_value=communities):
            response = _post_process_billing(app_with_cron_secret)

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["communities"] == 3
        assert payload["processed"] == 0, (
            "the cron must not count communities as processed billing runs"
        )

    def test_states_that_billing_runs_are_not_activated(self, app_with_cron_secret):
        with patch.object(database, "get_active_communities", return_value=[]):
            response = _post_process_billing(app_with_cron_secret)

        payload = response.get_json()
        assert payload["activated"] is False
        assert payload["status"] == "not_activated"
        assert payload["reason"], "the response explains why nothing ran"
