# SPDX-License-Identifier: AGPL-3.0-or-later
"""Remaining app-review regressions for PR #758."""

import json
import os
from copy import deepcopy
from unittest.mock import patch

import pytest

import member_invoices
import vnb_calculated_values as exchange
from app import create_app
from tests.native_environment import isolated_environment
from tests.test_billing_member_invoices import INVOICE_ROW
from tests.test_vnb_calculated_values import Repository, delivery


@pytest.fixture(autouse=True)
def isolated_app_environment():
    with patch.dict(
        os.environ,
        isolated_environment(
            {
                "ADMIN_TOKEN": "synthetic-secret",
                "APP_BASE_URL": "http://localhost:5003",
                "PUBLIC_SITE_URL": "https://openleg.ch",
                "SECRET_KEY": "synthetic-test-key",
                "REDIS_URL": "memory://",
                "OPENLEG_RELEASE_READ_ONLY": "0",
            }
        ),
        clear=True,
    ):
        yield


@pytest.mark.parametrize("view", ["detail", "list"])
@pytest.mark.parametrize("encoding", ["utc", "local"])
def test_last_day_invoice_remains_accessible(monkeypatch, view, encoding):
    row = deepcopy(INVOICE_ROW)
    start, end = (
        ("2026-07-31T22:00:00+00:00", "2026-08-31T22:00:00+00:00")
        if encoding == "utc"
        else ("2026-08-01T00:00:00+02:00", "2026-09-01T00:00:00+02:00")
    )
    row["provenance_snapshot"].update(period_start=start, period_end=end)
    row.update(issue_date="2026-08-31", due_date="2026-09-30")
    monkeypatch.setattr(
        member_invoices.db, "get_invoice_for_participant", lambda *_: row
    )
    monkeypatch.setattr(
        member_invoices.db, "get_invoices_for_participant", lambda *_: [row]
    )
    result = (
        member_invoices.detail_view(42, "building-session")
        if view == "detail"
        else member_invoices.list_view("building-session")
    )
    assert result
    if view == "detail":
        assert result["period_start"] == "2026-08-01"
        assert result["period_end"] == "2026-09-01"


@pytest.mark.parametrize("invalid_first", [False, True])
def test_unsupported_extra_record_cannot_alias_a_valid_replay(invalid_first):
    repo = Repository()
    valid = delivery()
    invalid = deepcopy(valid)
    invalid["records"].append(
        {
            **valid["records"][0],
            "participant_id": "extra-synthetic",
            "value": "0.0000004",
        }
    )

    def submit(payload):
        return exchange.accept_delivery(
            payload, transport="file", evidence=b"synthetic replay", repository=repo
        )

    first, second = (invalid, valid) if invalid_first else (valid, invalid)
    first_result, second_result = submit(first), submit(second)
    bad, good = (
        (first_result, second_result)
        if invalid_first
        else (second_result, first_result)
    )
    assert bad["status"] == "partially_invalid"
    assert good["status"] == "accepted"
    assert bad["fingerprint"] != good["fingerprint"]
    assert bad["id"] != good["id"]
    assert submit(invalid)["id"] == bad["id"]
    assert submit(valid)["id"] == good["id"]
    changed = deepcopy(invalid)
    changed["records"][-1]["value"] = "0.0000003"
    assert submit(changed)["fingerprint"] != bad["fingerprint"]


@pytest.mark.parametrize(
    "numeric_literal", ["0.500000000000000000001", "0.00000100000000000000000001"]
)
def test_raw_json_numeric_precision_is_preserved(monkeypatch, numeric_literal):
    monkeypatch.setenv("ADMIN_TOKEN", "synthetic-secret")
    web = create_app(
        {"TESTING": True, "RATELIMIT_STORAGE_URI": "memory://"},
        load_environment=False,
        check_database=False,
    )
    payload = {"delivery": delivery(), "transport": "api"}
    payload["delivery"]["records"][0]["value"] = "RAW_SYNTHETIC_NUMBER"
    encoded = json.dumps(payload).replace('"RAW_SYNTHETIC_NUMBER"', numeric_literal)
    repo = Repository()
    real_accept = exchange.accept_delivery

    def accept(*args, **kwargs):
        return real_accept(*args, **kwargs, repository=repo)

    with patch("vnb_calculated_values.accept_delivery", side_effect=accept):
        response = web.test_client().post(
            "/admin/vnb-calculated-values/dietikon",
            data=encoded,
            content_type="application/json",
            headers={"X-Admin-Token": "synthetic-secret"},
        )
    assert response.status_code == 422
    assert any(
        d["code"] == "unsupported_quantity_precision"
        for d in response.get_json()["diagnostics"]
    )


@pytest.mark.parametrize("issue_day", ["2026-08-31", "2026-09-01"])
def test_non_midnight_end_keeps_its_last_covered_day(monkeypatch, issue_day):
    from datetime import date, timedelta

    row = deepcopy(INVOICE_ROW)
    row["provenance_snapshot"].update(
        period_start="2026-08-01T00:00:00+02:00",
        period_end="2026-09-01T12:00:00+02:00",
    )
    row.update(
        issue_date=issue_day,
        due_date=(date.fromisoformat(issue_day) + timedelta(days=30)).isoformat(),
    )
    monkeypatch.setattr(
        member_invoices.db, "get_invoice_for_participant", lambda *_: row
    )
    if issue_day == "2026-08-31":
        with pytest.raises(member_invoices.MemberInvoiceDataError):
            member_invoices.detail_view(42, "building-session")
    else:
        assert (
            member_invoices.detail_view(42, "building-session")["period_end"]
            == "2026-09-01"
        )


def test_fractional_json_metadata_returns_validation_error(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "synthetic-secret")
    web = create_app(
        {"TESTING": True, "RATELIMIT_STORAGE_URI": "memory://"},
        load_environment=False,
        check_database=False,
    )
    payload = {"delivery": delivery(), "transport": "api"}
    payload["delivery"]["contract_version"] = 0.5
    repo = Repository()
    real_accept = exchange.accept_delivery

    def accept(*args, **kwargs):
        return real_accept(*args, **kwargs, repository=repo)

    with patch("vnb_calculated_values.accept_delivery", side_effect=accept):
        response = web.test_client().post(
            "/admin/vnb-calculated-values/dietikon",
            data=json.dumps(payload),
            content_type="application/json",
            headers={"X-Admin-Token": "synthetic-secret"},
        )
    assert response.status_code == 422
    assert any(
        d["code"] == "unsupported_contract" for d in response.get_json()["diagnostics"]
    )


@pytest.mark.parametrize("numeric_literal", ["1e400", "-1e400", "1e1000000"])
def test_numeric_overflow_keeps_nonfinite_refusal(monkeypatch, numeric_literal):
    monkeypatch.setenv("ADMIN_TOKEN", "synthetic-secret")
    web = create_app(
        {"TESTING": True, "RATELIMIT_STORAGE_URI": "memory://"},
        load_environment=False,
        check_database=False,
    )
    payload = {"delivery": delivery(), "transport": "api"}
    payload["delivery"]["records"][0]["value"] = "RAW_OVERFLOW"
    encoded = json.dumps(payload).replace('"RAW_OVERFLOW"', numeric_literal)
    repo = Repository()
    real_accept = exchange.accept_delivery

    def accept(*args, **kwargs):
        return real_accept(*args, **kwargs, repository=repo)

    with patch("vnb_calculated_values.accept_delivery", side_effect=accept):
        response = web.test_client().post(
            "/admin/vnb-calculated-values/dietikon",
            data=encoded,
            content_type="application/json",
            headers={"X-Admin-Token": "synthetic-secret"},
        )
    assert response.status_code == 422
    assert response.get_json()["status"] == "partially_invalid"
    assert (
        len(response.get_json()["normalized_records"])
        == len(payload["delivery"]["records"]) - 1
    )
