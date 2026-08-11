# SPDX-License-Identifier: AGPL-3.0-or-later
"""Schema contract for the SDAT metering point tables.

E66 files carry one series per (metering point, direction, product channel).
One physical point can be both a consumption and a production point, so the
readings key must include direction; without it the second series silently
overwrites the first. measured_at is TIMESTAMPTZ because the source is UTC and
a naive local key would collide on the October DST repeat.
"""

import os
import re

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _schema_source() -> str:
    with open(
        os.path.join(PROJECT_ROOT, "store/schema.py"), encoding="utf-8"
    ) as handle:
        return handle.read()


def _create_block(source: str, table: str) -> str:
    match = re.search(
        rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\)\s*\"\"\"",
        source,
        flags=re.DOTALL,
    )
    assert match, f"CREATE TABLE {table} block not found"
    return match.group(1)


def test_metering_points_is_keyed_on_the_point_id():
    block = _create_block(_schema_source(), "metering_points")
    assert re.search(r"metering_point_id VARCHAR\(64\) PRIMARY KEY", block), (
        "metering_points must be keyed on metering_point_id"
    )


def test_metering_points_keeps_vnb_and_openleg_community_ids_apart():
    block = _create_block(_schema_source(), "metering_points")
    assert re.search(r"vnb_community_id VARCHAR\(64\)", block), (
        "the VNB CommunityID needs its own column"
    )
    assert re.search(r"community_id VARCHAR\(64\) REFERENCES communities", block), (
        "community_id must reference the OpenLEG communities table"
    )


def test_metering_points_does_not_cascade_deletes_from_buildings():
    block = _create_block(_schema_source(), "metering_points")
    assert "ON DELETE SET NULL" in block, (
        "deleting a building must not delete VNB measurement history"
    )


def test_readings_measured_at_is_timestamptz():
    block = _create_block(_schema_source(), "metering_point_readings")
    assert re.search(r"measured_at TIMESTAMPTZ NOT NULL", block), (
        "measured_at must be TIMESTAMPTZ; the SDAT source is UTC"
    )


def test_readings_unique_key_includes_direction():
    block = _create_block(_schema_source(), "metering_point_readings")
    assert re.search(
        r"UNIQUE\s*\(metering_point_id,\s*direction,\s*measured_at\)", block
    ), (
        "the unique key must include direction; one point can be both a "
        "consumption and a production point at the same instant"
    )


def test_readings_direction_is_constrained():
    block = _create_block(_schema_source(), "metering_point_readings")
    assert "consumption" in block and "production" in block
    assert "CHECK" in block, "direction needs a CHECK constraint"


def test_readings_reference_the_point_registry():
    block = _create_block(_schema_source(), "metering_point_readings")
    assert "REFERENCES metering_points" in block


def test_readings_carry_the_three_channels_and_provenance():
    block = _create_block(_schema_source(), "metering_point_readings")
    for column in ("total_kwh", "grid_kwh", "community_kwh"):
        assert re.search(rf"{column} NUMERIC\(12, 4\)", block), (
            f"{column} must be NUMERIC(12, 4)"
        )
    assert "condition_code" in block
    assert "source_document_id" in block
    assert "resolution_minutes" in block


def test_sdat_imports_is_keyed_on_the_document_id():
    block = _create_block(_schema_source(), "sdat_imports")
    assert re.search(r"document_id VARCHAR\(64\) NOT NULL UNIQUE", block), (
        "the file ledger must be keyed on the SDAT document id"
    )
    for column in ("new_count", "corrected_count", "row_count"):
        assert column in block


def test_tables_are_created_before_the_completion_log():
    source = _schema_source()
    log_line = 'logger.info("[DB] Tables and indexes created successfully")'
    for table in ("metering_points", "metering_point_readings", "sdat_imports"):
        ddl = f"CREATE TABLE IF NOT EXISTS {table} ("
        assert source.index(ddl) < source.index(log_line), (
            f"{table} DDL must run inside create_tables()"
        )


def test_metering_indexes_exist():
    source = _schema_source()
    for index in (
        "idx_metering_points_building",
        "idx_metering_points_community",
        "idx_mpr_measured_at",
        "idx_mpr_document",
        "idx_sdat_imports_period",
    ):
        assert f"CREATE INDEX IF NOT EXISTS {index}" in source, f"missing index {index}"
