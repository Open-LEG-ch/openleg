# SPDX-License-Identifier: AGPL-3.0-or-later
"""Repository contract for SDAT metering points and readings.

Daily E66 deliveries overlap by four of five days, so the same interval is
written again and again. The upsert must key on point, direction and time,
skip rows whose values did not move, and report how many rows were new versus
corrected so an import can be audited without a revision table.
"""

import logging
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import database
from store import metering

MEASURED_AT = datetime(2026, 1, 5, 23, 0, tzinfo=timezone.utc)
POINT = "CH000000000000000000000000000001"


class _FakeCursor:
    def __init__(self, rows=None, one=None, required_sql=(), expected_params=None):
        self.rows = rows or []
        self.one = one
        self.executed = []
        self.required_sql = tuple(part.lower() for part in required_sql)
        self.expected_params = expected_params

    def execute(self, query, params=None):
        normalized = " ".join(query.split()).lower() if isinstance(query, str) else ""
        if isinstance(query, str) and ("XX" in query or "%S" in query):
            raise ValueError("database rejected malformed query")
        if any(part not in normalized for part in self.required_sql):
            raise ValueError("database rejected malformed query")
        if self.expected_params is not None and params != self.expected_params:
            raise ValueError("database rejected query parameters")
        self.executed.append((query, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.one

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _conn_ctx(cursor):
    @contextmanager
    def _factory():
        yield _FakeConnection(cursor)

    return _factory


def _broken_conn():
    @contextmanager
    def _factory():
        raise RuntimeError("db down")
        yield

    return _factory


def _row(sequence=1, total="0.100"):
    return {
        "metering_point_id": POINT,
        "direction": "consumption",
        "measured_at": MEASURED_AT + timedelta(minutes=15 * (sequence - 1)),
        "resolution_minutes": 15,
        "total_kwh": Decimal(total),
        "grid_kwh": Decimal("0.060"),
        "community_kwh": Decimal("0.040"),
        "condition_code": None,
    }


def _capture_execute_values(monkeypatch, returned):
    """Record the SQL and values handed to execute_values."""
    calls = []

    def _fake(cur, sql, values, page_size=None, fetch=False):
        calls.append(
            {
                "cur": cur,
                "sql": sql,
                "values": list(values),
                "page_size": page_size,
                "fetch": fetch,
            }
        )
        return returned if fetch else None

    import psycopg2.extras

    monkeypatch.setattr(psycopg2.extras, "execute_values", _fake)
    return calls


# ==== Re-export contract ====


def test_database_reexports_are_identical_objects():
    for name in (
        "get_billable_period_snapshot",
        "upsert_metering_points",
        "get_metering_points",
        "get_metering_point",
        "get_unassigned_period_metering_point_ids",
        "save_metering_point_readings",
        "get_metering_point_readings",
        "get_metering_point_reading_stats",
        "record_sdat_import",
        "get_sdat_import",
    ):
        assert getattr(database, name) is getattr(metering, name), (
            f"database.{name} must be the store.metering object"
        )


def test_store_metering_imports_without_database_bootstrap():
    result = subprocess.run(
        [sys.executable, "-c", "import store.metering; print('ok')"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


# ==== The upsert ====


def test_readings_upsert_keys_on_point_direction_and_time(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    calls = _capture_execute_values(monkeypatch, [])

    metering.save_metering_point_readings([_row()], source_document_id="DOC-1")

    readings_sql = calls[-1]["sql"]
    assert "ON CONFLICT (metering_point_id, direction, measured_at)" in readings_sql, (
        "one point can be both consumer and producer; direction belongs in the key"
    )


def test_readings_upsert_skips_rows_whose_values_did_not_move(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    calls = _capture_execute_values(monkeypatch, [])

    metering.save_metering_point_readings([_row()])

    readings_sql = calls[-1]["sql"]
    assert "IS DISTINCT FROM" in readings_sql, (
        "overlapping deliveries rewrite mostly identical rows; skip the unchanged"
    )


def test_readings_upsert_keeps_resolution_and_provenance_corrections(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    calls = _capture_execute_values(monkeypatch, [])

    metering.save_metering_point_readings([_row()])

    readings_sql = calls[-1]["sql"]
    assert "metering_point_readings.resolution_minutes" in readings_sql
    assert "metering_point_readings.source_document_id" in readings_sql


def test_readings_upsert_registers_points_before_readings(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    calls = _capture_execute_values(monkeypatch, [])

    metering.save_metering_point_readings([_row()])

    assert len(calls) == 2, "expected a point stub insert then the readings insert"
    assert "INSERT INTO metering_points" in calls[0]["sql"]
    assert "ON CONFLICT (metering_point_id) DO NOTHING" in calls[0]["sql"]
    assert "INSERT INTO metering_point_readings" in calls[1]["sql"]
    assert calls[0]["cur"] is cur
    assert calls[1]["cur"] is cur


def test_readings_upsert_reports_counts_and_correction_samples(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    returned = [
        {
            "metering_point_id": POINT,
            "direction": "consumption",
            "measured_at": MEASURED_AT + timedelta(minutes=15 * offset),
            "inserted": offset == 0,
        }
        for offset in range(25)
    ]
    _capture_execute_values(monkeypatch, returned)

    result = metering.save_metering_point_readings(
        [_row(sequence) for sequence in range(1, 27)]
    )

    assert result["written"] == 26
    assert result["new"] == 1
    assert result["corrected"] == 24
    assert result["unchanged"] == 1
    assert len(result["samples"]) == 20
    assert result["samples"][0] == (
        POINT,
        "consumption",
        MEASURED_AT + timedelta(minutes=15),
    )
    assert result["samples"][-1] == (
        POINT,
        "consumption",
        MEASURED_AT + timedelta(minutes=15 * 20),
    )
    assert (POINT, "consumption", MEASURED_AT) not in result["samples"]


def test_readings_upsert_uses_fallback_source_document_id_and_page_size(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    calls = _capture_execute_values(monkeypatch, [])

    row_with_default = _row(1)
    row_with_default.pop("source_document_id", None)
    row_with_override = _row(2)
    row_with_override["source_document_id"] = "DOC-ROW"

    metering.save_metering_point_readings(
        [row_with_default, row_with_override],
        source_document_id="DOC-DEFAULT",
    )

    assert calls[1]["page_size"] == 1000
    assert calls[1]["values"] == [
        (
            POINT,
            "consumption",
            MEASURED_AT,
            15,
            Decimal("0.100"),
            Decimal("0.060"),
            Decimal("0.040"),
            None,
            "DOC-DEFAULT",
        ),
        (
            POINT,
            "consumption",
            MEASURED_AT + timedelta(minutes=15),
            15,
            Decimal("0.100"),
            Decimal("0.060"),
            Decimal("0.040"),
            None,
            "DOC-ROW",
        ),
    ]


def test_readings_upsert_honours_the_default_batch_boundary(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    calls = _capture_execute_values(monkeypatch, [])

    metering.save_metering_point_readings([_row(index) for index in range(1, 5002)])

    reading_calls = [call for call in calls if call["fetch"]]
    assert [len(call["values"]) for call in reading_calls] == [5000, 1]


def test_readings_upsert_dedupes_repeated_keys(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    calls = _capture_execute_values(monkeypatch, [])

    duplicate = _row(1, total="0.900")
    metering.save_metering_point_readings([_row(1), duplicate])

    values = calls[-1]["values"]
    assert len(values) == 1, (
        "a repeated conflict key in one INSERT aborts the whole statement"
    )
    assert Decimal("0.900") in values[0], "the last occurrence must win"


def test_readings_upsert_accepts_an_iterator(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    calls = _capture_execute_values(monkeypatch, [])

    metering.save_metering_point_readings(iter([_row(1), _row(2)]))

    assert len(calls[-1]["values"]) == 2


def test_saving_no_rows_touches_no_database(monkeypatch):
    monkeypatch.setattr(database, "get_connection", _broken_conn())
    result = metering.save_metering_point_readings([])
    assert result == {
        "written": 0,
        "new": 0,
        "corrected": 0,
        "unchanged": 0,
        "samples": [],
    }


# ==== Reads ====


def test_get_readings_applies_direction_and_time_window(monkeypatch):
    cur = _FakeCursor(
        rows=[{"metering_point_id": POINT}],
        required_sql=(
            "select * from metering_point_readings where metering_point_id = %s",
            "and direction = %s",
            "and measured_at >= %s",
            "and measured_at <= %s",
            "order by measured_at desc limit %s",
        ),
        expected_params=[POINT, "production", MEASURED_AT, MEASURED_AT, 50],
    )
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    readings = metering.get_metering_point_readings(
        POINT, direction="production", start=MEASURED_AT, end=MEASURED_AT, limit=50
    )

    query, params = cur.executed[0]
    assert "FROM metering_point_readings" in query
    assert "direction = %s" in query
    assert "measured_at >= %s" in query and "measured_at <= %s" in query
    assert params == [POINT, "production", MEASURED_AT, MEASURED_AT, 50]
    assert readings == [{"metering_point_id": POINT}]


def test_get_readings_uses_the_documented_default_limit(monkeypatch):
    cur = _FakeCursor(
        rows=[{"metering_point_id": POINT}],
        required_sql=("order by measured_at desc limit %s",),
        expected_params=[POINT, 1000],
    )
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert metering.get_metering_point_readings(POINT) == [{"metering_point_id": POINT}]


def test_get_readings_coerces_numerics_to_float(monkeypatch):
    cur = _FakeCursor(
        rows=[
            {
                "metering_point_id": POINT,
                "total_kwh": Decimal("0.100"),
                "grid_kwh": Decimal("0.060"),
                "community_kwh": None,
            }
        ]
    )
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    row = metering.get_metering_point_readings(POINT)[0]
    assert type(row["total_kwh"]) is float
    assert type(row["grid_kwh"]) is float
    assert row["community_kwh"] is None, "a missing channel stays missing"


def test_get_metering_points_filters_by_community(monkeypatch):
    cur = _FakeCursor(rows=[])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    metering.get_metering_points(community_id="leg-1")

    query, params = cur.executed[0]
    assert "FROM metering_points" in query
    assert "community_id = %s" in query
    assert "leg-1" in params


def test_get_metering_points_defaults_to_active_sorted_rows(monkeypatch):
    cur = _FakeCursor(
        rows=[
            {"metering_point_id": "CH001", "active": True},
            {"metering_point_id": "CH002", "active": True},
        ],
        required_sql=(
            "select * from metering_points where 1 = 1",
            "and active = true",
            "order by metering_point_id",
        ),
    )
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    points = metering.get_metering_points()

    query, params = cur.executed[0]
    assert "active = TRUE" in query
    assert "ORDER BY metering_point_id" in query
    assert params == []
    assert points == [
        {"metering_point_id": "CH001", "active": True},
        {"metering_point_id": "CH002", "active": True},
    ]


def test_get_metering_points_can_include_inactive_rows(monkeypatch):
    cur = _FakeCursor(rows=[])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    metering.get_metering_points(active_only=False)

    query, _ = cur.executed[0]
    assert "active = TRUE" not in query


def test_get_metering_point_returns_the_requested_point(monkeypatch):
    row = {"metering_point_id": POINT, "alias": "Haus 1"}
    cur = _FakeCursor(
        one=row,
        required_sql=("select * from metering_points where metering_point_id = %s",),
        expected_params=(POINT,),
    )
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert metering.get_metering_point(POINT) == row
    assert cur.executed[0][1] == (POINT,)


def test_get_metering_point_returns_none_for_an_absent_row(monkeypatch):
    cur = _FakeCursor(one=None)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert metering.get_metering_point("CH-missing") is None


def test_get_metering_points_returns_an_empty_list_when_nothing_is_stored(monkeypatch):
    cur = _FakeCursor(rows=[])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert metering.get_metering_points() == []


def test_get_readings_returns_an_empty_list_when_nothing_is_recorded(monkeypatch):
    cur = _FakeCursor(rows=[])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert metering.get_metering_point_readings(POINT) == []


# ==== Registry enrichment ====


@pytest.mark.parametrize("expected_directions", [None, []])
def test_upsert_points_passes_no_direction_overwrite_for_none_or_empty_list(
    monkeypatch, expected_directions
):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    calls = _capture_execute_values(monkeypatch, [])

    metering.upsert_metering_points(
        [
            {
                "metering_point_id": POINT,
                "alias": "Haus 1",
                "expected_directions": expected_directions,
            }
        ]
    )

    assert isinstance(calls[-1]["sql"], str) and calls[-1]["sql"].strip()
    assert calls[-1]["values"][0][-1] is None


def test_upsert_points_passes_every_registry_value(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    calls = _capture_execute_values(monkeypatch, [])
    point = {
        "metering_point_id": POINT,
        "vnb_community_id": "VNB-LEG-1",
        "community_id": "LEG-1",
        "building_id": "BLD-1",
        "alias": "Haus 1",
        "address": "Dorfstrasse 1",
    }

    assert metering.upsert_metering_points([point]) == 1
    assert calls[0]["cur"] is cur
    assert calls[0]["values"] == [
        (
            POINT,
            "VNB-LEG-1",
            "LEG-1",
            "BLD-1",
            "Haus 1",
            "Dorfstrasse 1",
            None,
        )
    ]


def test_upsert_points_ignores_entries_without_an_identifier(monkeypatch):
    monkeypatch.setattr(database, "get_connection", _broken_conn())

    assert metering.upsert_metering_points([{}, {"alias": "Haus 1"}]) == 0


def test_upsert_points_canonicalises_declared_directions(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    calls = _capture_execute_values(monkeypatch, [])

    result = metering.upsert_metering_points(
        [
            {
                "metering_point_id": POINT,
                "expected_directions": [
                    "production",
                    "consumption",
                    "production",
                ],
            }
        ]
    )

    assert result == 1
    assert calls[0]["values"][0][-1] == ["consumption", "production"]


@pytest.mark.parametrize("expected_directions", ["", ["consumption", 1]])
def test_upsert_points_rejects_values_outside_direction_type_contract(
    monkeypatch, caplog, expected_directions
):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    calls = _capture_execute_values(monkeypatch, [])

    with caplog.at_level(logging.ERROR):
        result = metering.upsert_metering_points(
            [
                {
                    "metering_point_id": POINT,
                    "expected_directions": expected_directions,
                }
            ]
        )

    assert result == 0
    assert calls == []
    assert caplog.records[-1].getMessage() == (
        "[DB] Error upserting metering points: "
        "expected_directions erwartet list[str] oder None"
    )


def test_upsert_points_rejects_unknown_declared_directions(monkeypatch, caplog):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    calls = _capture_execute_values(monkeypatch, [])

    with caplog.at_level(logging.ERROR):
        result = metering.upsert_metering_points(
            [
                {
                    "metering_point_id": POINT,
                    "expected_directions": ["export", "feed-in"],
                }
            ]
        )

    assert result == 0
    assert calls == []
    assert caplog.records[-1].getMessage() == (
        "[DB] Error upserting metering points: "
        "Unbekannte Messrichtung(en): export, feed-in"
    )


# ==== File ledger ====


def test_record_sdat_import_passes_the_complete_ledger_record(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    created_at = datetime(2026, 1, 6, 1, 0, tzinfo=timezone.utc)
    period_end = MEASURED_AT + timedelta(days=5)
    result = metering.record_sdat_import(
        {
            "document_id": "DOC-1",
            "doc_type": "E66",
            "file_name": "delivery.xml",
            "vnb_community_id": "VNB-LEG-1",
            "document_created_at": created_at,
            "period_start": MEASURED_AT,
            "period_end": period_end,
            "block_count": 2,
            "row_count": 6,
            "new_count": 4,
            "corrected_count": 2,
        }
    )

    query, params = cur.executed[0]
    assert result is True
    assert "INSERT INTO sdat_imports" in query
    assert "ON CONFLICT (document_id) DO UPDATE" in query
    assert params == (
        "DOC-1",
        "E66",
        "delivery.xml",
        "VNB-LEG-1",
        created_at,
        MEASURED_AT,
        period_end,
        2,
        6,
        4,
        2,
    )


def test_record_sdat_import_defaults_missing_counts_to_zero(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert metering.record_sdat_import({"document_id": "DOC-1"}) is True
    assert cur.executed[0][1] == (
        "DOC-1",
        None,
        None,
        None,
        None,
        None,
        None,
        0,
        0,
        0,
        0,
    )


def test_get_sdat_import_returns_none_when_absent(monkeypatch):
    cur = _FakeCursor(one=None)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    assert metering.get_sdat_import("nope") is None


def test_get_sdat_import_returns_the_requested_ledger_row(monkeypatch):
    row = {"document_id": "DOC-1", "doc_type": "E66"}
    cur = _FakeCursor(
        one=row,
        required_sql=("select * from sdat_imports where document_id = %s",),
        expected_params=("DOC-1",),
    )
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert metering.get_sdat_import("DOC-1") == row
    assert cur.executed[0][1] == ("DOC-1",)


# ==== Billing reads ====


def test_community_points_expose_membership_status(monkeypatch):
    """Billing may only bill a confirmed member, so the status must come along."""
    cur = _FakeCursor(
        rows=[
            {
                "metering_point_id": POINT,
                "building_id": "BLD-A",
                "alias": None,
                "expected_directions": ["consumption"],
                "member_status": "confirmed",
            }
        ]
    )
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    points = metering.get_community_metering_points("COMM-1")

    query, params = cur.executed[0]
    assert "LEFT JOIN community_members" in query, (
        "a point mapped to a building with no membership row must still be "
        "returned, so the adapter can name it"
    )
    assert "active = TRUE" in query
    assert params == ("COMM-1",)
    assert points[0]["member_status"] == "confirmed"
    assert points[0]["expected_directions"] == ["consumption"]
    assert "mp.expected_directions" in query


def test_unassigned_period_point_lookup_propagates_storage_failure(monkeypatch):
    monkeypatch.setattr(database, "get_connection", _broken_conn())

    with pytest.raises(RuntimeError, match="db down"):
        metering.get_unassigned_period_metering_point_ids(
            "COMM-1", MEASURED_AT, MEASURED_AT + timedelta(minutes=15)
        )


def test_unassigned_period_point_lookup_executes_and_returns_ordered_ids(monkeypatch):
    start = MEASURED_AT
    end = start + timedelta(hours=1)
    expected_params = (start, end, start, end, "COMM-1")
    cur = _FakeCursor(
        rows=[
            {"metering_point_id": "point-a"},
            {"metering_point_id": "point-b"},
        ],
        expected_params=expected_params,
    )
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    found = metering.get_unassigned_period_metering_point_ids("COMM-1", start, end)

    query, params = cur.executed[0]
    assert isinstance(query, str) and query.strip()
    assert params == expected_params
    assert found == ["point-a", "point-b"]


def test_period_readings_use_a_half_open_interval(monkeypatch):
    cur = _FakeCursor(rows=[])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    start = MEASURED_AT
    end = MEASURED_AT + timedelta(minutes=45)

    metering.get_period_readings("COMM-1", start, end)

    query, params = cur.executed[0]
    assert "measured_at >= %s" in query
    assert "measured_at < %s" in query, (
        "the period end must be exclusive; an inclusive end double-counts the "
        "boundary interval across two periods"
    )
    assert params == ("COMM-1", start, end)


def test_period_readings_convert_numerics_to_float(monkeypatch):
    cur = _FakeCursor(rows=[_row(total="0.250")])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    readings = metering.get_period_readings(
        "COMM-1", MEASURED_AT, MEASURED_AT + timedelta(minutes=15)
    )

    assert isinstance(readings[0]["total_kwh"], float)
    assert not isinstance(readings[0]["total_kwh"], Decimal)


def test_reading_stats_return_the_database_aggregate(monkeypatch):
    row = {
        "total_readings": 3,
        "total_points": 1,
        "first_reading": MEASURED_AT,
        "last_reading": MEASURED_AT + timedelta(minutes=30),
        "total_kwh": Decimal("0.300"),
        "grid_kwh": Decimal("0.180"),
        "community_kwh": Decimal("0.120"),
    }
    cur = _FakeCursor(
        one=row,
        required_sql=(
            "select count(*) as total_readings",
            "where metering_point_id = %s",
        ),
        expected_params=[POINT],
    )
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    assert metering.get_metering_point_reading_stats(POINT) == row
    query, params = cur.executed[0]
    assert "WHERE metering_point_id = %s" in query
    assert params == [POINT]


# ==== Failure behaviour ====


def test_connection_failure_returns_safe_defaults(monkeypatch):
    monkeypatch.setattr(database, "get_connection", _broken_conn())

    assert metering.get_community_metering_points("COMM-1") == []
    assert (
        metering.get_period_readings(
            "COMM-1", MEASURED_AT, MEASURED_AT + timedelta(minutes=15)
        )
        == []
    )
    assert metering.get_metering_point_readings(POINT) == []
    assert metering.get_metering_points() == []
    assert metering.get_metering_point(POINT) is None
    assert metering.get_metering_point_reading_stats() == {}
    assert metering.get_sdat_import("DOC-1") is None
    assert metering.record_sdat_import({"document_id": "DOC-1"}) is False
    assert metering.upsert_metering_points([{"metering_point_id": POINT}]) == 0
    assert metering.save_metering_point_readings([_row()])["written"] == 0


def test_save_metering_point_readings_logs_the_database_error(monkeypatch, caplog):
    monkeypatch.setattr(database, "get_connection", _broken_conn())

    with caplog.at_level(logging.ERROR):
        result = metering.save_metering_point_readings([_row()])

    assert result == {
        "written": 0,
        "new": 0,
        "corrected": 0,
        "unchanged": 0,
        "samples": [],
    }
    assert caplog.messages == ["[DB] Error saving metering point readings: db down"]


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (
            lambda: metering.upsert_metering_points([{"metering_point_id": POINT}]),
            "[DB] Error upserting metering points: db down",
        ),
        (
            lambda: metering.get_metering_points(),
            "[DB] Error getting metering points: db down",
        ),
        (
            lambda: metering.get_metering_point(POINT),
            "[DB] Error getting metering point: db down",
        ),
        (
            lambda: metering.get_metering_point_readings(POINT),
            "[DB] Error getting metering point readings: db down",
        ),
        (
            lambda: metering.get_metering_point_reading_stats(POINT),
            "[DB] Error getting metering point stats: db down",
        ),
        (
            lambda: metering.record_sdat_import({"document_id": "DOC-1"}),
            "[DB] Error recording SDAT import: db down",
        ),
        (
            lambda: metering.get_sdat_import("DOC-1"),
            "[DB] Error getting SDAT import: db down",
        ),
        (
            metering.get_sdat_import_index,
            "[DB] Error getting SDAT import index: db down",
        ),
    ],
)
def test_repository_failures_log_the_operation(monkeypatch, caplog, call, message):
    monkeypatch.setattr(database, "get_connection", _broken_conn())

    with caplog.at_level(logging.ERROR):
        call()

    assert caplog.messages == [message]


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (
            lambda: metering.get_community_metering_points("COMM-1"),
            "[DB] Error getting community metering points: db down",
        ),
        (
            lambda: metering.get_period_readings(
                "COMM-1", MEASURED_AT, MEASURED_AT + timedelta(minutes=15)
            ),
            "[DB] Error getting period readings: db down",
        ),
    ],
)
def test_billing_read_failures_log_the_operation(monkeypatch, caplog, call, message):
    monkeypatch.setattr(database, "get_connection", _broken_conn())

    with caplog.at_level(logging.ERROR):
        call()

    assert caplog.messages == [message]


# ==== The import index ====


def test_import_index_returns_both_keys_in_one_query(monkeypatch):
    cur = _FakeCursor(
        rows=[
            {"document_id": "DOC-1", "file_name": "a.xml"},
            {"document_id": "DOC-2", "file_name": "b.xml"},
        ],
        required_sql=("select document_id, file_name from sdat_imports",),
    )
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    index = metering.get_sdat_import_index()

    assert index["document_ids"] == frozenset({"DOC-1", "DOC-2"})
    assert index["file_names"] == frozenset({"a.xml", "b.xml"})
    assert len(cur.executed) == 1, (
        "the point of the index is one query per run, not one per file"
    )


def test_import_index_tolerates_rows_without_a_file_name(monkeypatch):
    # file_name is nullable, so a legacy row must not put None into the set and
    # make an unnamed file look settled.
    cur = _FakeCursor(rows=[{"document_id": "DOC-1", "file_name": None}])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    index = metering.get_sdat_import_index()

    assert index["file_names"] == frozenset()
    assert index["document_ids"] == frozenset({"DOC-1"})


def test_import_index_is_empty_when_the_ledger_cannot_be_read(monkeypatch):
    # Empty means "do the work". Anything else would skip a delivery because a
    # query failed.
    monkeypatch.setattr(database, "get_connection", _broken_conn())

    index = metering.get_sdat_import_index()

    assert index == {"document_ids": frozenset(), "file_names": frozenset()}


# ==== Billable period snapshot ====


class _SnapshotCursor:
    """Dispatches rows per query so the snapshot's three reads differ."""

    def __init__(self, points=None, readings=None, unassigned=None, fail_at=None):
        self._points = points or []
        self._readings = readings or []
        self._unassigned = unassigned or []
        self._fail_at = fail_at
        self.executed = []

    def execute(self, query, params=None):
        if self._fail_at is not None and len(self.executed) == self._fail_at:
            raise RuntimeError("db down")
        self.executed.append((query, params))

    def fetchall(self):
        normalized = " ".join(self.executed[-1][0].split()).lower()
        if "left join community_members" in normalized:
            return self._points
        if "select distinct mp.metering_point_id" in normalized:
            return self._unassigned
        return self._readings

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _SnapshotConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.cursor_calls = 0

    def cursor(self):
        self.cursor_calls += 1
        return self._cursor


def _snapshot_conn(monkeypatch, cursor):
    """One fake connection per get_connection call; counts how often asked."""
    connection = _SnapshotConnection(cursor)
    calls = {"count": 0}

    @contextmanager
    def _factory():
        calls["count"] += 1
        yield connection

    monkeypatch.setattr(database, "get_connection", _factory)
    return connection, calls


SNAPSHOT_START = MEASURED_AT
SNAPSHOT_END = MEASURED_AT + timedelta(hours=1)


def test_snapshot_sets_repeatable_read_read_only_before_any_query(monkeypatch):
    cur = _SnapshotCursor()
    _snapshot_conn(monkeypatch, cur)

    metering.get_billable_period_snapshot("COMM-1", SNAPSHOT_START, SNAPSHOT_END)

    first_query, first_params = cur.executed[0]
    normalized = " ".join(first_query.split()).lower()
    assert normalized == (
        "set transaction isolation level repeatable read read only"
    ), "the stable snapshot must be established before any read runs"
    assert first_params is None
    assert "read committed" not in normalized
    for query, _ in cur.executed[1:]:
        assert "set transaction" not in " ".join(query.split()).lower()
        assert query.lstrip().upper().startswith("SELECT")


def test_snapshot_runs_every_read_on_one_connection_and_cursor(monkeypatch):
    cur = _SnapshotCursor()
    connection, calls = _snapshot_conn(monkeypatch, cur)

    metering.get_billable_period_snapshot("COMM-1", SNAPSHOT_START, SNAPSHOT_END)

    assert calls["count"] == 1, (
        "one connection, one transaction: three reads on separate "
        "connections could never share a stable snapshot"
    )
    assert connection.cursor_calls == 1
    assert len(cur.executed) == 4, "isolation setup plus exactly three reads"


def test_snapshot_reads_use_the_half_open_period(monkeypatch):
    cur = _SnapshotCursor()
    _snapshot_conn(monkeypatch, cur)

    metering.get_billable_period_snapshot("COMM-1", SNAPSHOT_START, SNAPSHOT_END)

    readings_query, readings_params = cur.executed[2]
    assert "measured_at >= %s" in readings_query
    assert "measured_at < %s" in readings_query, (
        "the period end must be exclusive; an inclusive end double-counts "
        "the boundary interval across two periods"
    )
    assert readings_params == ("COMM-1", SNAPSHOT_START, SNAPSHOT_END)

    unassigned_query, unassigned_params = cur.executed[3]
    assert "measured_at >= %s" in unassigned_query
    assert "measured_at < %s" in unassigned_query
    assert unassigned_params == (
        SNAPSHOT_START,
        SNAPSHOT_END,
        SNAPSHOT_START,
        SNAPSHOT_END,
        "COMM-1",
    )


def test_snapshot_returns_the_full_aggregate(monkeypatch):
    points = [
        {
            "metering_point_id": POINT,
            "building_id": "BLD-A",
            "alias": None,
            "expected_directions": ["consumption"],
            "vnb_community_id": "VNB-LEG-1",
            "member_status": "confirmed",
        }
    ]
    cur = _SnapshotCursor(
        points=points,
        readings=[_row(total="0.250")],
        unassigned=[{"metering_point_id": "point-unassigned"}],
    )
    _snapshot_conn(monkeypatch, cur)

    snapshot = metering.get_billable_period_snapshot(
        "COMM-1", SNAPSHOT_START, SNAPSHOT_END
    )

    assert snapshot == {
        "points": points,
        "readings": [metering._floatify(_row(total="0.250"))],
        "unassigned_point_ids": ["point-unassigned"],
    }
    assert isinstance(snapshot["readings"][0]["total_kwh"], float)

    points_query, points_params = cur.executed[1]
    assert "mp.expected_directions" in points_query, (
        "billing validates against the declared directions, so they must "
        "come with the snapshot"
    )
    assert "mp.vnb_community_id" in points_query, (
        "the VNB provenance ties the snapshot to the delivering utility"
    )
    assert "active = TRUE" in points_query
    assert points_params == ("COMM-1",)


def test_snapshot_propagates_a_connection_failure(monkeypatch):
    monkeypatch.setattr(database, "get_connection", _broken_conn())

    with pytest.raises(RuntimeError, match="db down"):
        metering.get_billable_period_snapshot("COMM-1", SNAPSHOT_START, SNAPSHOT_END)


@pytest.mark.parametrize("fail_at", [0, 1, 2, 3])
def test_snapshot_propagates_a_failure_mid_transaction(monkeypatch, fail_at):
    """A failed read must not be swallowed into an empty or partial result."""
    cur = _SnapshotCursor(fail_at=fail_at)
    _snapshot_conn(monkeypatch, cur)

    with pytest.raises(RuntimeError, match="db down"):
        metering.get_billable_period_snapshot("COMM-1", SNAPSHOT_START, SNAPSHOT_END)
