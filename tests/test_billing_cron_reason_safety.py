# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract: the billing cron exposes only the reviewed balancing reason."""

import pytest

import billing_runner
import database
from tests import test_billing_cron_contract

app_with_cron_secret = test_billing_cron_contract.app_with_cron_secret

SECRET = "private password=hunter2"


@pytest.mark.parametrize(
    "code,reason,expected_reason",
    [
        ("vnb_allocation_mismatch", "internal-review-token", None),
        ("billing_tariff_missing", "vnb_balancing_policy_required", None),
        (
            "vnb_allocation_mismatch",
            "vnb_balancing_policy_required",
            "vnb_balancing_policy_required",
        ),
        ("vnb_allocation_mismatch", None, None),
    ],
    ids=[
        "unknown_reason_is_dropped",
        "allowed_reason_needs_mismatch_code",
        "allowed_reason_survives_on_mismatch",
        "missing_reason_is_omitted",
    ],
)
def test_cron_publishes_only_the_reviewed_balancing_reason(
    app_with_cron_secret, monkeypatch, caplog, code, reason, expected_reason
):
    monkeypatch.setattr(
        database, "get_active_communities", lambda: [{"community_id": "c1"}]
    )

    def fail(*args):
        raise billing_runner.BillingRunError(SECRET, code=code, reason=reason)

    monkeypatch.setattr(billing_runner, "run_billing_period", fail)

    response = test_billing_cron_contract._post_process_billing(app_with_cron_secret)

    assert response.status_code == 200
    payload = response.get_json()
    failure = {"community_id": "c1", "error": code}
    if expected_reason is not None:
        failure["reason"] = expected_reason
    assert payload["status"] == "partial_failure"
    assert payload["processed"] == 0
    assert payload["failed"] == 1
    assert payload["failures"] == [failure]
    assert SECRET not in response.get_data(as_text=True)
    assert SECRET not in caplog.text
    assert "internal-review-token" not in response.get_data(as_text=True)
    assert "internal-review-token" not in caplog.text
    assert "Billing run failed for community c1" in caplog.text
