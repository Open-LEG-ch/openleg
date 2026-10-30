# SPDX-License-Identifier: AGPL-3.0-or-later
"""Schema contract for scheduled SDAT ingestion."""

from pathlib import Path

SCHEMA = (Path(__file__).resolve().parents[1] / "store" / "schema.py").read_text()


def test_schedule_and_run_tables_are_tenant_scoped_and_time_safe():
    assert "CREATE TABLE IF NOT EXISTS sdat_ingestion_schedules" in SCHEMA
    assert "territory VARCHAR(64) PRIMARY KEY" in SCHEMA
    assert "timezone VARCHAR(64) NOT NULL" in SCHEMA
    assert "local_time TIME NOT NULL" in SCHEMA
    assert "CHECK (max_attempts BETWEEN 1 AND 5)" in SCHEMA
    assert "CHECK (retry_seconds BETWEEN 0 AND 300)" in SCHEMA
    assert "CREATE TABLE IF NOT EXISTS sdat_ingestion_runs" in SCHEMA
    assert "started_at TIMESTAMPTZ NOT NULL" in SCHEMA
    assert "finished_at TIMESTAMPTZ NOT NULL" in SCHEMA


def test_run_history_contains_only_aggregate_observability_fields():
    start = SCHEMA.index("CREATE TABLE IF NOT EXISTS sdat_ingestion_runs")
    end = SCHEMA.index("CREATE INDEX IF NOT EXISTS idx_sdat_ingestion_runs_latest")
    block = SCHEMA[start:end]
    for field in (
        "downloaded_files",
        "imported_files",
        "imported_readings",
        "error_code",
    ):
        assert field in block
    for private_field in ("password", "metering_point_id", "file_content"):
        assert private_field not in block
