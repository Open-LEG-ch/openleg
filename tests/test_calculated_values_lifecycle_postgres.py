# SPDX-License-Identifier: AGPL-3.0-or-later
"""Retire VNB evidence without erasing the original delivery or issuing invoices."""

import os
from datetime import datetime
from unittest.mock import patch

import pytest

from tests import test_interest_postgres

interest_database = test_interest_postgres.interest_database
pytestmark = pytest.mark.integration


@pytest.fixture
def calculated_client(interest_database):
    db = interest_database
    assert db.save_building(
        "member",
        "member@example.invalid",
        {"address": "Synthetic meter 1", "lat": 47.2, "lon": 8.2},
        {},
        verified=True,
        city_id="dietikon",
    )
    cid = db.create_community_record("Synthetic LEG", "member", "simple", "")
    assert (
        db.upsert_metering_points(
            [
                {
                    "metering_point_id": "SYNTHETIC-METER-1",
                    "community_id": cid,
                    "building_id": "member",
                    "expected_directions": ["consumption"],
                }
            ]
        )
        == 1
    )
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": os.environ["DATABASE_URL"],
            "REDIS_URL": "",
            "ADMIN_TOKEN": "synthetic-admin",
        },
        clear=True,
    ):
        from app import create_app

        app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "synthetic-lifecycle",
                "RATELIMIT_STORAGE_URI": "memory://",
            },
            load_environment=False,
            check_database=False,
        )
        yield (
            app.test_client(),
            db,
            {
                "contract_version": "vnb-calculated-values/1",
                "format_version": "json/1",
                "community_id": cid,
                "period_start": "2026-10-01T00:00:00+02:00",
                "period_end": "2026-10-01T00:15:00+02:00",
                "timezone": "Europe/Zurich",
                "source": "Synthetic VNB",
                "vnb_case_id": "SYNTHETIC-ORIGINAL",
                "unit": "kWh",
                "records": [
                    {
                        "measured_at": "2026-10-01T00:00:00+02:00",
                        "participant_id": "member",
                        "direction": "consumption",
                        "value": "0.25",
                    }
                ],
            },
        )


def test_withdrawal_preserves_source_and_allows_a_corrected_delivery(calculated_client):
    client, db, payload = calculated_client
    headers = {"X-Admin-Token": "synthetic-admin"}
    endpoint = "/admin/vnb-calculated-values/dietikon"
    original_response = client.post(
        endpoint, headers=headers, json={"delivery": payload, "transport": "file"}
    )
    assert original_response.status_code == 201
    original = original_response.get_json()
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT evidence_bytes, evidence_sha256, normalized_records FROM vnb_calculated_values_deliveries WHERE id = %s",
            (original["id"],),
        )
        source = dict(cur.fetchone())
    withdrawal = f"{endpoint}/{original['id']}/withdrawals"
    request_headers = {**headers, "Idempotency-Key": "synthetic-withdrawal-1"}

    response = client.post(
        withdrawal,
        headers=request_headers,
        json={"reason": "Synthetic VNB withdrew the original delivery"},
    )

    assert response.status_code == 201
    assert response.get_json()["action"] == "withdrawn"
    assert response.headers["Cache-Control"] == "no-store"
    start, end = (
        datetime.fromisoformat(payload[key]) for key in ("period_start", "period_end")
    )
    assert (
        db.get_validated_calculated_values(payload["community_id"], start, end) is None
    )
    replay = client.post(
        withdrawal,
        headers=request_headers,
        json={"reason": "Synthetic VNB withdrew the original delivery"},
    )
    assert replay.status_code == 200
    assert replay.get_json()["replayed"] is True
    corrected = {
        **payload,
        "vnb_case_id": "SYNTHETIC-CORRECTED",
        "records": [{**payload["records"][0], "value": "0.30"}],
    }
    replacement = client.post(
        endpoint, headers=headers, json={"delivery": corrected, "transport": "file"}
    )
    assert replacement.status_code == 201
    assert (
        db.get_validated_calculated_values(payload["community_id"], start, end)["id"]
        == replacement.get_json()["id"]
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT evidence_bytes, evidence_sha256, normalized_records FROM vnb_calculated_values_deliveries WHERE id = %s",
            (original["id"],),
        )
        retained = dict(cur.fetchone())
    assert bytes(retained["evidence_bytes"]) == bytes(source["evidence_bytes"])
    assert retained["evidence_sha256"] == source["evidence_sha256"]
    assert retained["normalized_records"] == source["normalized_records"]
