# SPDX-License-Identifier: AGPL-3.0-or-later
"""Repository contract for SDAT metering points and readings.

Daily E66 deliveries overlap by four of five days, so the same interval is
written again and again. The upsert must key on point, direction and time,
skip rows whose values did not move, and report how many rows were new versus
corrected so an import can be audited without a revision table.
"""

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
    def __init__(self, rows=None, one=None):
        self.rows = rows or []
        self.one = one
        self.executed = []

    def execute(self, query, params=None):
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
        calls.append({"sql": sql, "values": list(values), "fetch": fetch})
        return returned if fetch else None

    import psycopg2.extras

    monkeypatch.setattr(psycopg2.extras, "execute_values", _fake)
    return calls


# ==== Re-export contract ====


def test_database_reexports_are_identical_objects():
    for name in (
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


def test_readings_upsert_reports_new_and_corrected_counts(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    returned = [
        {
            "metering_point_id": POINT,
            "direction": "consumption",
            "measured_at": MEASURED_AT,
            "inserted": True,
        },
        {
            "metering_point_id": POINT,
            "direction": "consumption",
            "measured_at": MEASURED_AT,
            "inserted": False,
        },
    ]
    _capture_execute_values(monkeypatch, returned)

    result = metering.save_metering_point_readings(
        [_row(1), _row(2), _row(3), _row(4), _row(5)]
    )

    assert result["written"] == 5
    assert result["new"] == 1
    assert result["corrected"] == 1
    assert result["unchanged"] == 3


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
    assert result["written"] == 0


# ==== Reads ====


def test_get_readings_applies_direction_and_time_window(monkeypatch):
    cur = _FakeCursor(rows=[])
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    metering.get_metering_point_readings(
        POINT, direction="production", start=MEASURED_AT, end=MEASURED_AT, limit=50
    )

    query, params = cur.executed[0]
    assert "FROM metering_point_readings" in query
    assert "direction = %s" in query
    assert "measured_at >= %s" in query and "measured_at <= %s" in query
    assert params == [POINT, "production", MEASURED_AT, MEASURED_AT, 50]


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


# ==== Registry enrichment ====


def test_upsert_points_does_not_blank_existing_fields(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    calls = _capture_execute_values(monkeypatch, [])

    metering.upsert_metering_points([{"metering_point_id": POINT, "alias": "Haus 1"}])

    sql = calls[-1]["sql"]
    assert "COALESCE" in sql, (
        "a re-run with blank columns must not erase existing registry data"
    )


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


def test_upsert_points_rejects_unknown_declared_directions(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    calls = _capture_execute_values(monkeypatch, [])

    result = metering.upsert_metering_points(
        [{"metering_point_id": POINT, "expected_directions": ["export"]}]
    )

    assert result == 0
    assert calls == []


# ==== File ledger ====


def test_record_sdat_import_is_idempotent(monkeypatch):
    cur = _FakeCursor()
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))

    metering.record_sdat_import({"document_id": "DOC-1", "doc_type": "E66"})

    query, _ = cur.executed[0]
    assert "INSERT INTO sdat_imports" in query
    assert "ON CONFLICT (document_id) DO UPDATE" in query


def test_get_sdat_import_returns_none_when_absent(monkeypatch):
    cur = _FakeCursor(one=None)
    monkeypatch.setattr(database, "get_connection", _conn_ctx(cur))
    assert metering.get_sdat_import("nope") is None


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


# ==== The import index ====


def test_import_index_returns_both_keys_in_one_query(monkeypatch):
    cur = _FakeCursor(
        rows=[
            {"document_id": "DOC-1", "file_name": "a.xml"},
            {"document_id": "DOC-2", "file_name": "b.xml"},
        ]
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
