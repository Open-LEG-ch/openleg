# SPDX-License-Identifier: AGPL-3.0-or-later
"""Versioned export and transactional restore of one LEG operational record."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal

SCHEMA_VERSION = "openleg-community-archive/1"
SOURCE_PROVENANCE = "OpenLEG PostgreSQL operational store"


class ArchiveError(ValueError):
    """The requested archive operation cannot be completed safely."""


def _json_value(value):
    if isinstance(value, bytes):
        return {"$binary": base64.b64encode(value).decode("ascii")}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


def _python_value(value):
    if isinstance(value, list):
        return [_python_value(item) for item in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {"$binary"}:
        return base64.b64decode(value["$binary"], validate=True)
    if set(value) == {"$decimal"}:
        return Decimal(value["$decimal"])
    if set(value) == {"$datetime"}:
        return datetime.fromisoformat(value["$datetime"])
    if set(value) == {"$date"}:
        return date.fromisoformat(value["$date"])
    return {key: _python_value(item) for key, item in value.items()}


def _canonical(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def export_community_archive(community_id: str, *, store=None) -> bytes:
    """Return a deterministic JSON archive for exactly one community."""
    store = store or PostgresArchiveStore()
    datasets = store.export_community(community_id)
    if not datasets:
        raise ArchiveError("LEG not found")
    encoded = {table: _json_value(datasets.get(table, [])) for table, _, _ in _DATASETS}
    hashes = {
        name: f"sha256:{hashlib.sha256(_canonical(rows)).hexdigest()}"
        for name, rows in encoded.items()
    }
    archive = {
        "manifest": {
            "schema_version": SCHEMA_VERSION,
            "community_id": community_id,
            "format": "application/json",
            "source_provenance": SOURCE_PROVENANCE,
            "units": {"energy": "kWh", "power": "kWp", "money": "CHF"},
            "time_zones": ["Europe/Zurich", "UTC"],
            "hashes": hashes,
        },
        "datasets": encoded,
    }
    return _canonical(archive)


def restore_community_archive(archive: bytes | str, *, dry_run=False, store=None):
    """Validate and optionally restore an archive in one store transaction."""
    store = store or PostgresArchiveStore()
    errors = []
    try:
        payload = json.loads(archive)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return {
            "valid": False,
            "errors": ["Archive is not valid JSON"],
            "conflicts": [],
        }
    manifest = payload.get("manifest") if isinstance(payload, dict) else None
    datasets = payload.get("datasets") if isinstance(payload, dict) else None
    if not isinstance(manifest, dict) or not isinstance(datasets, dict):
        return {
            "valid": False,
            "errors": ["Manifest or datasets missing"],
            "conflicts": [],
        }
    version = manifest.get("schema_version")
    if version != SCHEMA_VERSION:
        errors.append(f"Unsupported schema version: {version}")
    hashes = manifest.get("hashes", {})
    expected_datasets = {table for table, _, _ in _DATASETS}
    if set(datasets) != expected_datasets:
        errors.append("Archive dataset list does not match schema")
    for name, rows in datasets.items():
        actual = f"sha256:{hashlib.sha256(_canonical(rows)).hexdigest()}"
        if hashes.get(name) != actual:
            errors.append(f"Hash mismatch: {name}")
    community_rows = datasets.get("communities")
    community_id = manifest.get("community_id")
    if (
        not isinstance(community_rows, list)
        or len(community_rows) != 1
        or not isinstance(community_rows[0], dict)
        or community_rows[0].get("community_id") != community_id
    ):
        errors.append("Community record does not match manifest")

    def dataset_rows(name):
        value = datasets.get(name, [])
        return value if isinstance(value, list) else []

    member_ids = {
        row.get("building_id")
        for row in dataset_rows("community_members")
        if isinstance(row, dict)
    }
    metering_ids = {
        row.get("metering_point_id")
        for row in dataset_rows("metering_points")
        if isinstance(row, dict)
    }
    source_document_ids = {
        row.get("source_document_id")
        for row in dataset_rows("metering_point_readings")
        if isinstance(row, dict) and row.get("source_document_id") is not None
    }
    period_ids = {
        row.get("id")
        for row in dataset_rows("billing_periods")
        if isinstance(row, dict)
    }
    invoice_ids = {
        row.get("id") for row in dataset_rows("invoices") if isinstance(row, dict)
    }
    for name, records in datasets.items():
        if name not in {table for table, _, _ in _DATASETS}:
            errors.append(f"Unsupported dataset: {name}")
        if not isinstance(records, list) or any(
            not isinstance(row, dict) for row in records
        ):
            errors.append(f"Invalid dataset: {name}")
            continue
        for row in records:
            if "community_id" in row and row["community_id"] != community_id:
                errors.append(f"Community scope violation: {name}")
                break
    for name in ("buildings", "consents", "data_consents", "meter_readings"):
        if any(row.get("building_id") not in member_ids for row in dataset_rows(name)):
            errors.append(f"Member scope violation: {name}")
    if any(
        row.get("metering_point_id") not in metering_ids
        for row in dataset_rows("metering_point_readings")
    ):
        errors.append("Metering scope violation: metering_point_readings")
    if any(
        row.get("document_id") not in source_document_ids
        for row in dataset_rows("sdat_imports")
    ):
        errors.append("Metering scope violation: sdat_imports")
    if any(
        row.get("metering_point_id") not in metering_ids
        for row in dataset_rows("sdat_veracity_flags")
    ):
        errors.append("Metering scope violation: sdat_veracity_flags")
    if any(
        row.get("billing_period_id") not in period_ids
        for row in dataset_rows("billing_line_items")
    ):
        errors.append("Billing scope violation: billing_line_items")
    if any(
        row.get("invoice_id") not in invoice_ids
        for row in dataset_rows("invoice_lifecycle_events")
    ):
        errors.append("Invoice scope violation: invoice_lifecycle_events")
    if not errors and hasattr(store, "validate_records"):
        errors.extend(store.validate_records(datasets))
    try:
        decoded = _python_value(datasets)
    except (ValueError, TypeError):
        errors.append("Invalid tagged value")
    conflicts = (
        store.find_conflicts(community_id, datasets)
        if not errors and hasattr(store, "find_conflicts")
        else []
    )
    result = {"valid": not errors, "errors": errors, "conflicts": conflicts}
    if errors or conflicts or dry_run:
        return result
    store.restore_community(decoded)
    return {**result, "restored": True}


# Restore order follows foreign keys. All identifiers are fixed here, never read
# from the archive, so table and column interpolation cannot become SQL input.
_DATASETS = (
    (
        "buildings",
        "building_id IN (SELECT building_id FROM community_members WHERE community_id = %s)",
        "community_id",
    ),
    ("communities", "community_id = %s", "community_id"),
    ("community_members", "community_id = %s", "community_id"),
    (
        "consents",
        "building_id IN (SELECT building_id FROM community_members WHERE community_id = %s)",
        "community_id",
    ),
    (
        "data_consents",
        "building_id IN (SELECT building_id FROM community_members WHERE community_id = %s)",
        "community_id",
    ),
    (
        "meter_readings",
        "building_id IN (SELECT building_id FROM community_members WHERE community_id = %s)",
        "community_id",
    ),
    ("community_documents", "community_id = %s", "community_id"),
    ("leg_documents", "community_id = %s", "community_id"),
    ("correspondence_log", "community_id = %s", "community_id"),
    ("metering_points", "community_id = %s", "community_id"),
    (
        "sdat_imports",
        "document_id IN (SELECT DISTINCT r.source_document_id FROM metering_point_readings r JOIN metering_points p USING (metering_point_id) WHERE p.community_id = %s AND r.source_document_id IS NOT NULL)",
        "community_id",
    ),
    (
        "metering_point_readings",
        "metering_point_id IN (SELECT metering_point_id FROM metering_points WHERE community_id = %s)",
        "community_id",
    ),
    (
        "sdat_veracity_flags",
        "metering_point_id IN (SELECT metering_point_id FROM metering_points WHERE community_id = %s)",
        "community_id",
    ),
    ("billing_tariffs", "community_id = %s", "community_id"),
    ("billing_periods", "community_id = %s", "community_id"),
    (
        "billing_line_items",
        "billing_period_id IN (SELECT id FROM billing_periods WHERE community_id = %s)",
        "community_id",
    ),
    ("invoices", "community_id = %s", "community_id"),
    ("invoice_lifecycle_events", "community_id = %s", "community_id"),
    ("invoice_delivery_jobs", "community_id = %s", "community_id"),
    ("invoice_corrections", "community_id = %s", "community_id"),
)


class PostgresArchiveStore:
    """PostgreSQL adapter for the community archive interface."""

    @staticmethod
    def _connection():
        import database

        return database.get_connection()

    def export_community(self, community_id):
        result = {}
        with self._connection() as conn, conn.cursor() as cur:
            for table, predicate, _ in _DATASETS:
                cur.execute(f"SELECT * FROM {table} WHERE {predicate}", (community_id,))
                result[table] = [dict(row) for row in cur.fetchall()]
        return result if result["communities"] else None

    def find_conflicts(self, community_id, datasets):
        with self._connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM communities WHERE community_id = %s", (community_id,)
            )
            existing = cur.fetchone()
            if existing is None:
                return []
            archived = _python_value(datasets["communities"])[0]
            return (
                []
                if all(existing[key] == value for key, value in archived.items())
                else [f"Community already exists with different data: {community_id}"]
            )

    @staticmethod
    def _allowed_columns(cur):
        allowed = {}
        cur.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public'"
        )
        for record in cur.fetchall():
            allowed.setdefault(record["table_name"], set()).add(record["column_name"])
        return allowed

    def validate_records(self, datasets):
        with self._connection() as conn, conn.cursor() as cur:
            allowed = self._allowed_columns(cur)
        errors = []
        for table, rows in datasets.items():
            for row in rows if isinstance(rows, list) else []:
                unknown = set(row) - allowed.get(table, set())
                if unknown:
                    errors.append(
                        f"Unknown columns in {table}: {', '.join(sorted(unknown))}"
                    )
        return errors

    def restore_community(self, datasets):
        with self._connection() as conn, conn.cursor() as cur:
            allowed_columns = self._allowed_columns(cur)
            cur.execute(
                "SELECT tc.table_name, kcu.column_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "ON tc.constraint_name = kcu.constraint_name "
                "AND tc.table_schema = kcu.table_schema "
                "WHERE tc.table_schema = 'public' "
                "AND tc.constraint_type = 'PRIMARY KEY' "
                "ORDER BY kcu.ordinal_position"
            )
            primary_keys = {}
            for record in cur.fetchall():
                primary_keys.setdefault(record["table_name"], []).append(
                    record["column_name"]
                )
            for table, _, _ in _DATASETS:
                for row in datasets.get(table, []):
                    if not isinstance(row, dict) or not row:
                        raise ArchiveError(f"Invalid record: {table}")
                    columns = tuple(row)
                    if not set(columns) <= allowed_columns.get(table, set()):
                        raise ArchiveError(f"Unknown column: {table}")
                    placeholders = ", ".join(["%s"] * len(columns))
                    cur.execute(
                        f"INSERT INTO {table} ({', '.join(columns)}) "
                        f"VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                        tuple(row[column] for column in columns),
                    )
                    if cur.rowcount == 0:
                        keys = primary_keys.get(table, [])
                        if not keys or any(key not in row for key in keys):
                            raise ArchiveError(f"Conflicting record: {table}")
                        where = " AND ".join(f"{key} = %s" for key in keys)
                        cur.execute(
                            f"SELECT {', '.join(columns)} FROM {table} WHERE {where}",
                            tuple(row[key] for key in keys),
                        )
                        existing = cur.fetchone()
                        if existing is None or any(
                            existing[column] != row[column] for column in columns
                        ):
                            raise ArchiveError(f"Conflicting record: {table}")
