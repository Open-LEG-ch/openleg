# SPDX-License-Identifier: AGPL-3.0-or-later
"""Safe operator diagnostics through the authenticated billing cron."""

import psycopg2
import pytest

import billing_runner
import database
from store import billing
from tests import test_billing_cron_contract, test_billing_runner

app_with_cron_secret = test_billing_cron_contract.app_with_cron_secret


def test_cron_identifies_a_missing_billing_tariff(app_with_cron_secret, monkeypatch):
    monkeypatch.setattr(
        database, "get_active_communities", lambda: [{"community_id": "c1"}]
    )
    monkeypatch.setattr(database, "get_billing_policy", lambda *args: None)

    response = test_billing_cron_contract._post_process_billing(app_with_cron_secret)

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "partial_failure"
    assert payload["processed"] == 0
    assert payload["failures"] == [
        {"community_id": "c1", "error": "billing_tariff_missing"}
    ]


@pytest.mark.parametrize(
    "blocker",
    [
        "vnb_evidence_missing",
        "metering_readings_invalid",
        "vnb_allocation_mismatch",
        "billing_inputs_changed",
        "battery_configuration_invalid",
        "billing_store_unavailable",
        "billing_provenance_missing",
    ],
)
def test_cron_reports_known_blockers_without_persisting(
    app_with_cron_secret, monkeypatch, blocker
):
    saved = test_billing_runner._install_billing_fixture(
        monkeypatch,
        consumption_community_kwh=0.25 if blocker == "vnb_allocation_mismatch" else 0.5,
    )
    monkeypatch.setattr(
        database, "get_active_communities", lambda: [{"community_id": "c1"}]
    )
    monkeypatch.setattr(
        billing_runner,
        "previous_complete_month",
        lambda: (test_billing_runner.START, test_billing_runner.END),
    )
    if blocker == "vnb_evidence_missing":
        monkeypatch.setattr(database, "is_db_available", lambda: True)
        monkeypatch.setattr(
            database, "get_validated_calculated_values", lambda *args: None
        )
    elif blocker == "metering_readings_invalid":
        monkeypatch.setattr(
            database,
            "get_billable_period_snapshot",
            lambda *args: {"points": [], "readings": [], "unassigned_point_ids": []},
        )
    elif blocker == "billing_inputs_changed":
        monkeypatch.setattr(
            database,
            "get_billing_period_for_window",
            lambda *args: {"id": 1, "input_fingerprint": "previous"},
        )
    elif blocker == "battery_configuration_invalid":
        monkeypatch.setattr(database, "get_battery", lambda *args: {"capacity_kwh": 0})
    elif blocker == "billing_store_unavailable":

        def unavailable(*args):
            raise database.BillingStoreError("private database diagnostic")

        monkeypatch.setattr(database, "get_billing_policy", unavailable)
    elif blocker == "billing_provenance_missing":
        original_snapshot = database.get_billable_period_snapshot

        def without_provenance(*args):
            snapshot = original_snapshot(*args)
            return {
                **snapshot,
                "readings": [
                    {**row, "source_document_id": None} for row in snapshot["readings"]
                ],
            }

        monkeypatch.setattr(
            database, "get_billable_period_snapshot", without_provenance
        )

    response = test_billing_cron_contract._post_process_billing(app_with_cron_secret)

    assert response.status_code == 200
    assert response.json["processed"] == 0
    assert response.json["failures"] == [{"community_id": "c1", "error": blocker}]
    assert saved == []


@pytest.mark.parametrize(
    "code,expected",
    [
        ("vnb_evidence_missing", "vnb_evidence_missing"),
        ("private-source-token", "billing_run_failed"),
    ],
)
def test_cron_exposes_only_approved_codes(
    app_with_cron_secret, monkeypatch, caplog, code, expected
):
    secret = "private-metering-point password=private-database-token"
    monkeypatch.setattr(
        database, "get_active_communities", lambda: [{"community_id": "c1"}]
    )

    def fail(*args):
        raise billing_runner.BillingRunError(secret, code=code)

    monkeypatch.setattr(billing_runner, "run_billing_period", fail)

    response = test_billing_cron_contract._post_process_billing(app_with_cron_secret)

    assert response.json["failures"] == [{"community_id": "c1", "error": expected}]
    assert secret not in response.text
    assert secret not in caplog.text
    assert "private-source-token" not in response.text
    assert "private-source-token" not in caplog.text


@pytest.mark.parametrize("stage", ["discovery", "persistence"])
def test_real_store_failures_do_not_log_private_details(
    app_with_cron_secret, monkeypatch, caplog, stage
):
    secret = "private-metering-point password=private-database-token"
    if stage == "persistence":
        test_billing_runner._install_billing_fixture(monkeypatch)
        monkeypatch.setattr(
            database, "get_active_communities", lambda: [{"community_id": "c1"}]
        )
        monkeypatch.setattr(
            billing_runner,
            "previous_complete_month",
            lambda: (test_billing_runner.START, test_billing_runner.END),
        )
        monkeypatch.setattr(
            database, "save_billing_period", billing.save_billing_period
        )

    def unavailable():
        raise psycopg2.OperationalError(secret)

    monkeypatch.setattr(database, "get_connection", unavailable)

    response = test_billing_cron_contract._post_process_billing(app_with_cron_secret)

    if stage == "discovery":
        assert response.status_code == 503
        assert response.json == {
            "status": "error",
            "error": "billing_store_unavailable",
        }
    else:
        assert response.status_code == 200
        assert response.json["processed"] == 0
        assert response.json["failures"] == [
            {"community_id": "c1", "error": "billing_store_unavailable"}
        ]
    assert secret not in response.text
    assert secret not in caplog.text


def test_driver_failure_reports_safe_codes_for_every_community(
    app_with_cron_secret, monkeypatch, caplog
):
    secret = "private-metering-point password=private-database-token"
    monkeypatch.setattr(
        database,
        "get_active_communities",
        lambda: [
            {"community_id": "c1"},
            {"community_id": "c2"},
        ],
    )

    def unavailable():
        raise psycopg2.OperationalError(secret)

    monkeypatch.setattr(database, "get_connection", unavailable)

    response = test_billing_cron_contract._post_process_billing(app_with_cron_secret)

    assert response.status_code == 200
    assert response.json["failed"] == 2
    assert response.json["processed"] == 0
    assert response.json["failures"] == [
        {"community_id": "c1", "error": "billing_store_unavailable"},
        {"community_id": "c2", "error": "billing_store_unavailable"},
    ]
    assert secret not in response.text
    assert secret not in caplog.text
