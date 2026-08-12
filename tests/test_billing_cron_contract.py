# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract: billing cron counts only newly persisted draft periods."""

from unittest.mock import call, patch

import pytest

import app as app_module
import billing_runner
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
    def test_counts_only_newly_persisted_periods(self, app_with_cron_secret):
        communities = [
            {"community_id": "c1"},
            {"community_id": "c2"},
        ]
        with (
            patch.object(database, "get_active_communities", return_value=communities),
            patch.object(
                billing_runner,
                "previous_complete_month",
                return_value=("start", "end"),
            ),
            patch.object(
                billing_runner,
                "run_billing_period",
                side_effect=(
                    {"status": "created", "period_id": 1},
                    {"status": "already_processed", "period_id": 2},
                ),
            ) as run,
        ):
            response = _post_process_billing(app_with_cron_secret)

        assert response.status_code == 200
        payload = response.get_json()
        assert payload == {
            "activated": True,
            "status": "ok",
            "communities": 2,
            "processed": 1,
            "already_processed": 1,
            "failed": 0,
            "failures": [],
        }
        assert run.call_args_list == [
            call("c1", "start", "end"),
            call("c2", "start", "end"),
        ]

    def test_persistence_failure_is_redacted_and_not_counted(
        self, app_with_cron_secret, caplog
    ):
        secret_detail = "DATABASE_URL=postgres://private password=hunter2"
        with (
            patch.object(
                database,
                "get_active_communities",
                return_value=[{"community_id": "c1"}],
            ),
            patch.object(
                billing_runner,
                "previous_complete_month",
                return_value=("start", "end"),
            ),
            patch.object(
                billing_runner,
                "run_billing_period",
                side_effect=billing_runner.BillingRunError(secret_detail),
            ),
        ):
            response = _post_process_billing(app_with_cron_secret)

        payload = response.get_json()
        assert payload["processed"] == 0
        assert payload["failed"] == 1
        assert payload["failures"] == [
            {"community_id": "c1", "error": "billing_run_failed"}
        ]
        assert secret_detail not in response.get_data(as_text=True)
        assert secret_detail in caplog.text
