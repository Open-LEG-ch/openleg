# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bounded metering retries through the authenticated HTTP boundary."""

from unittest.mock import Mock, patch

import pytest
from flask import Flask

import operator_api
import sdat_ingestion

CLIENT = {
    "id": "retry-client",
    "community_id": "retry-community",
    "created_by": "retry-admin",
    "capabilities": ["metering.mutate"],
    "rate_limit_per_hour": 100,
}


@pytest.fixture
def retry_app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(operator_api.operator_api_bp)
    with (
        patch(
            "operator_api.db.get_operator_api_client_by_token_hash", return_value=CLIENT
        ),
        patch("operator_api.db.claim_operator_api_usage", return_value=True),
        patch("operator_api.db.operator_creator_is_confirmed_admin", return_value=True),
    ):
        yield app


def retry(app, key, job_id=7):
    with app.test_client() as client:
        return client.post(
            f"/api/operator/v1/communities/retry-community/metering/jobs/{job_id}/retry",
            headers={"Authorization": "Bearer test", "Idempotency-Key": key},
            json={},
        )


def test_http_retry_attempts_once_without_sleeping(retry_app, tmp_path, monkeypatch):
    monkeypatch.setenv("SWISSELDEX_SDAT_DIR", str(tmp_path))
    schedule = {
        "territory": "retry-tenant",
        "max_attempts": 5,
        "retry_seconds": 300,
        "local_dir": "retry-tenant",
    }
    original_run = sdat_ingestion.run
    sleeper = Mock()
    fetcher = Mock(side_effect=RuntimeError("offline"))
    store = Mock()
    store.acquire.return_value = True

    def run(territory, options, **kwargs):
        return original_run(
            territory,
            options,
            store=store,
            fetcher=fetcher,
            sleeper=sleeper,
            **kwargs,
        )

    with (
        patch(
            "operator_api.db.claim_operator_ingestion_retry",
            return_value={"schedule": schedule},
        ),
        patch("operator_api.db.complete_operator_ingestion_retry"),
        patch("operator_api.sdat_ingestion.run", side_effect=run) as runner,
    ):
        response = retry(retry_app, "first")

    assert response.status_code == 200
    assert response.json["status"] == "failure"
    assert response.json["attempts"] == 1
    assert fetcher.call_count == 1
    sleeper.assert_not_called()
    assert runner.call_args.args[0] == "retry-tenant"
    assert schedule["max_attempts"] == 5


def test_locked_territory_releases_the_claim_without_a_cooldown(retry_app):
    with (
        patch(
            "operator_api.db.claim_operator_ingestion_retry",
            return_value={"schedule": {"territory": "retry-tenant"}},
        ),
        patch("operator_api.db.complete_operator_ingestion_retry") as complete,
        patch("operator_api.db.release_operator_ingestion_retry") as release,
        patch(
            "operator_api.sdat_ingestion.run",
            return_value={"status": "locked", "territory": "retry-tenant"},
        ),
    ):
        response = retry(retry_app, "first")
    assert response.status_code == 409
    assert response.json["code"] == "retry_in_progress"
    complete.assert_not_called()
    release.assert_called_once_with("retry-community", 7, "first")
