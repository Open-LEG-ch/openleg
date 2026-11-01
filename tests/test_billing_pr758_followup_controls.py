# SPDX-License-Identifier: AGPL-3.0-or-later
"""Boundary controls for the three remaining PR #758 review repairs."""

import json
from copy import deepcopy
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

import member_invoices
import vnb_calculated_values as exchange
from app import create_app
from tests.test_billing_member_invoices import INVOICE_ROW
from tests.test_billing_pr758_review_followups import isolated_app_environment  # noqa: F401
from tests.test_vnb_calculated_values import Repository, delivery


@pytest.mark.parametrize("encoding", ["utc", "offset", "date", "datetime"])
def test_winter_last_covered_day_preserves_legacy_date_forms(monkeypatch, encoding):
    start, end = date(2026, 1, 1), date(2026, 2, 1)
    issue = end - timedelta(days=1)
    row = deepcopy(INVOICE_ROW)
    if encoding in {"utc", "offset", "datetime"}:
        zone = ZoneInfo("UTC" if encoding == "utc" else "Europe/Zurich")
        start, end = (
            datetime.combine(
                day, datetime.min.time(), ZoneInfo("Europe/Zurich")
            ).astimezone(zone)
            for day in (start, end)
        )
    if encoding != "datetime":
        start, end = start.isoformat(), end.isoformat()
    row["provenance_snapshot"].update(period_start=start, period_end=end)
    row.update(issue_date=issue, due_date=issue + timedelta(days=30))
    monkeypatch.setattr(
        member_invoices.db, "get_invoice_for_participant", lambda *_: row
    )
    result = member_invoices.detail_view(42, "building-session")
    assert result["period_start"] == "2026-01-01"
    assert result["period_end"] == "2026-02-01"


@pytest.mark.parametrize("problem", ["early", "empty", "reversed", "due", "overflow"])
def test_member_date_boundary_still_fails_closed(monkeypatch, problem):
    row = deepcopy(INVOICE_ROW)
    row["provenance_snapshot"].update(
        period_start="2026-08-01", period_end="2026-09-01"
    )
    issue = date(2026, 8, 30) if problem == "early" else date(2026, 8, 31)
    row.update(issue_date=issue, due_date=issue + timedelta(days=30))
    if problem in {"empty", "reversed"}:
        row["provenance_snapshot"]["period_end"] = (
            "2026-08-01" if problem == "empty" else "2026-07-31"
        )
    if problem == "due":
        row["due_date"] += timedelta(days=1)
    if problem == "overflow":
        row.update(issue_date=date.max, due_date=date.max)
    monkeypatch.setattr(
        member_invoices.db, "get_invoice_for_participant", lambda *_: row
    )
    with pytest.raises(member_invoices.MemberInvoiceDataError):
        member_invoices.detail_view(42, "building-session")


@pytest.fixture
def operator_client(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "synthetic-secret")
    web = create_app(
        {"TESTING": True, "RATELIMIT_STORAGE_URI": "memory://"},
        load_environment=False,
        check_database=False,
    )
    return web.test_client()


@pytest.mark.parametrize(
    "unit,literal,quoted,accepted",
    [
        ("kWh", "0.500001", False, True),
        ("kWh", "0.00000100000000000000000000", False, True),
        ("Wh", "500.001", False, True),
        ("kWh", "0.500000000000000000001", True, False),
        ("Wh", "500.000000000000000000001", False, False),
        ("Wh", "500.000000000000000000001", True, False),
    ],
)
def test_route_quantity_precision_and_original_evidence(
    operator_client, unit, literal, quoted, accepted
):
    payload = {"delivery": delivery(unit), "transport": "api"}
    if unit == "Wh":
        for record in payload["delivery"]["records"]:
            record["value"] = 250
    payload["delivery"]["territory"] = "other"
    payload["delivery"]["records"][0]["value"] = "RAW_NUMBER"
    encoded = (
        json.dumps(payload)
        .replace('"RAW_NUMBER"', json.dumps(literal) if quoted else literal)
        .encode()
    )
    repo = Repository()
    real_accept = exchange.accept_delivery
    with patch(
        "vnb_calculated_values.accept_delivery",
        side_effect=lambda *args, **kwargs: real_accept(
            *args, **kwargs, repository=repo
        ),
    ):
        response = operator_client.post(
            "/admin/vnb-calculated-values/dietikon",
            data=encoded,
            content_type="application/json",
            headers={"X-Admin-Token": "synthetic-secret"},
        )
    assert response.status_code == (201 if accepted else 422)
    assert response.headers["Cache-Control"] == "no-store"
    saved = next(iter(repo.saved.values()))
    assert saved["evidence_bytes"] == encoded
    assert saved["territory"] == "dietikon"
    json.dumps(
        {
            key: value
            for key, value in saved.items()
            if key not in {"period_start", "period_end", "evidence_bytes"}
        }
    )  # Persisted JSON must contain no Decimal values.
    if not accepted:
        assert "unsupported_quantity_precision" in {
            item["code"] for item in response.get_json()["diagnostics"]
        }


@pytest.mark.parametrize(
    "body,content_type",
    [
        (b'{"delivery":', "application/json"),
        (b'{"delivery":"\xff"}', "application/json"),
        (b"{}", "text/plain"),
        (b"null", "application/json"),
        (b"[]", "application/json"),
    ],
)
def test_route_invalid_json_keeps_payload_error(operator_client, body, content_type):
    with patch("vnb_calculated_values.accept_delivery") as accept:
        response = operator_client.post(
            "/admin/vnb-calculated-values/dietikon",
            data=body,
            content_type=content_type,
            headers={"X-Admin-Token": "synthetic-secret"},
        )
    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid_payload"}
    assert response.headers["Cache-Control"] == "no-store"
    accept.assert_not_called()


def test_route_keeps_legacy_numeric_metadata_json_serializable(operator_client):
    payload = {"delivery": delivery(), "transport": "file"}
    payload["delivery"].update(source=1.25, vnb_case_id=2.75)
    repo = Repository()
    real_accept = exchange.accept_delivery
    with patch(
        "vnb_calculated_values.accept_delivery",
        side_effect=lambda *args, **kwargs: real_accept(
            *args, **kwargs, repository=repo
        ),
    ):
        response = operator_client.post(
            "/admin/vnb-calculated-values/dietikon",
            json=payload,
            headers={"X-Admin-Token": "synthetic-secret"},
        )
    assert response.status_code == 201
    saved = next(iter(repo.saved.values()))
    assert type(saved["source"]) is float
    assert type(saved["vnb_case_id"]) is float


def test_original_836_accepted_fingerprint_is_frozen():
    result = exchange.accept_delivery(
        delivery(),
        transport="file",
        evidence=b"frozen original 836aef9",
        repository=Repository(),
    )
    assert result["status"] == "accepted"
    assert result["fingerprint"] == (
        "63d2b780659fdd66f9722db72f45a24be8f8a454c55813ab6ad5a05ed70d6ae7"
    )


def test_original_836_accepted_e66_fingerprint_and_source_are_frozen():
    payload = delivery()
    payload["format_version"] = "e66/1"
    quantities = {"total_kwh": "0.250", "community_kwh": "0.250", "grid_kwh": "0.000"}
    for record in payload["records"]:
        record.update(
            source_metering_point_id="synthetic-meter", source_quantities=quantities
        )
    repo = Repository()
    first = exchange.accept_delivery(
        payload,
        transport="file",
        evidence=b"synthetic E66",
        repository=repo,
        source_metadata={"document_status": "9"},
    )
    second = exchange.accept_delivery(
        payload,
        transport="api",
        evidence=b"different transport",
        repository=repo,
        source_metadata={"document_status": "9"},
    )
    assert first["status"] == "accepted"
    assert first["fingerprint"] == (
        "083edf13183b8e5b93c6cc6eb7c173853f2febf12586bfe68082fc7a0976c8e7"
    )
    assert second["id"] == first["id"]
    assert second["replayed"] is True
    assert second["normalized_records"][0]["source_quantities"] == quantities
    assert repo.saved[first["fingerprint"]]["evidence_bytes"] == b"synthetic E66"


@pytest.mark.parametrize("invalid_first", [False, True])
@pytest.mark.parametrize("problem", ["unit", "transport", "record"])
def test_refused_input_cannot_poison_accepted_replay(invalid_first, problem):
    repo = Repository()
    valid = delivery()
    invalid = deepcopy(valid)
    if problem == "unit":
        invalid["unit"] = "unsupported"
    elif problem == "record":
        invalid["records"].append({"value": "synthetic-private-invalid"})

    def submit(bad):
        return exchange.accept_delivery(
            invalid if bad else valid,
            transport="unsupported" if bad and problem == "transport" else "file",
            evidence=b"synthetic",
            repository=repo,
        )

    first, second = submit(invalid_first), submit(not invalid_first)
    bad, good = (first, second) if invalid_first else (second, first)
    assert bad["status"] != "accepted"
    assert good["status"] == "accepted"
    assert bad["fingerprint"] != good["fingerprint"]
    assert submit(True)["id"] == bad["id"]
    assert submit(False)["id"] == good["id"]
    assert "synthetic-private-invalid" not in repr(bad)
    if problem == "record":
        invalid["records"][-1]["value"] = "different-private-invalid"
        assert submit(True)["fingerprint"] != bad["fingerprint"]
