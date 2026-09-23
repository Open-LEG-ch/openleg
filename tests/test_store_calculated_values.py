# SPDX-License-Identifier: AGPL-3.0-or-later
"""Persistence contract for calculated VNB evidence."""

from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from store import calculated_values


class Cursor:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return next(self.rows)

    def fetchall(self):
        return list(self.rows)


class Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def test_replay_returns_existing_delivery_without_rewriting_evidence(monkeypatch):
    existing = {
        "id": 7,
        "content_fingerprint": "a" * 64,
        "diagnostics": [],
        "normalized_records": [],
        "replayed": True,
    }
    cursor = Cursor([None, None, existing])

    @contextmanager
    def connection():
        yield Connection(cursor)

    monkeypatch.setattr(calculated_values, "_get_connection", connection)
    delivery = {
        "contract_version": "vnb-calculated-values/1",
        "format_version": "json/1",
        "transport": "api",
        "territory": "dietikon",
        "community_id": "leg-1",
        "period_start": None,
        "period_end": None,
        "timezone": "Europe/Zurich",
        "source": "VNB",
        "vnb_case_id": "case-1",
        "fingerprint": "a" * 64,
        "evidence_sha256": "b" * 64,
        "evidence_bytes": b"private",
        "status": "accepted",
        "diagnostics": [],
        "normalized_records": [],
        "record_count": 0,
    }
    result = calculated_values.save_calculated_values_delivery(delivery)
    assert result["replayed"] is True
    assert "evidence_bytes" not in result
    assert (
        "ON CONFLICT (territory, content_fingerprint) DO NOTHING"
        in cursor.executed[2][0]
    )
    assert "pg_advisory_xact_lock" in cursor.executed[0][0]
    assert "content_fingerprint <> %s" in cursor.executed[1][0]
    assert cursor.executed[3][1] == ("dietikon", "a" * 64)


def test_operator_projection_scopes_by_tenant_and_omits_raw_fields(monkeypatch):
    cursor = Cursor(
        [
            {
                "id": 7,
                "content_fingerprint": "a" * 64,
                "diagnostics": [],
                "record_count": 20,
            }
        ]
    )

    @contextmanager
    def connection():
        yield Connection(cursor)

    monkeypatch.setattr(calculated_values, "_get_connection", connection)
    result = calculated_values.list_calculated_values_deliveries("dietikon", limit=5)
    assert result == [
        {
            "id": 7,
            "fingerprint": "a" * 64,
            "diagnostics": [],
            "record_count": 20,
        }
    ]
    query, params = cursor.executed[0]
    assert params == ("dietikon", 5)
    assert "evidence_bytes" not in query
    assert "normalized_records" not in query


def test_save_billing_period_persists_the_calculated_values_fingerprint(monkeypatch):
    from store import billing

    cursor = Cursor([{"id": 42}])

    @contextmanager
    def connection():
        yield Connection(cursor)

    monkeypatch.setattr(billing, "_get_connection", connection)
    period_id = billing.save_billing_period(
        "leg-1",
        datetime(2026, 10, 25, 0, 0, tzinfo=ZoneInfo("Europe/Zurich")),
        datetime(2026, 10, 26, 0, 0, tzinfo=ZoneInfo("Europe/Zurich")),
        {
            "total_production_kwh": 1.5,
            "total_allocated_kwh": 1.5,
            "total_network_discount_chf": 0.05,
            "input_fingerprint": "c" * 64,
            "calculated_values_fingerprint": "a" * 64,
            "line_items": [
                {
                    "participant_id": "building-a",
                    "item_type": "consumer_charge",
                    "quantity_kwh": 1.5,
                    "unit_price_chf_per_kwh": 0.12,
                    "amount_chf": 0.18,
                }
            ],
        },
    )

    assert period_id == 42
    query, params = cursor.executed[0]
    assert "INSERT INTO billing_periods" in query
    assert "calculated_values_fingerprint" in query
    assert params[0] == "leg-1"
    assert params[12] == "c" * 64
    # The fingerprint is the last bound column before the literal 'draft'.
    assert params[-1] == "a" * 64
