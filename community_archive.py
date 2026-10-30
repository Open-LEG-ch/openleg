# SPDX-License-Identifier: AGPL-3.0-or-later
"""Versioned export and transactional restore of one LEG operational record."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from itertools import groupby, islice

from psycopg2 import sql
from psycopg2.extras import Json, execute_values

LEGACY_SCHEMA_VERSION = "openleg-community-archive/1"
SCHEMA_VERSION = "openleg-community-archive/2"
SOURCE_PROVENANCE = "OpenLEG PostgreSQL operational store"


class ArchiveError(ValueError):
    """The requested archive operation cannot be completed safely."""


def _json_value(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
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
    if _validate_vnb_evidence(datasets):
        raise ArchiveError("VNB evidence cannot be archived safely")
    if _validate_operating_relations(datasets):
        raise ArchiveError("Operational record cannot be archived safely")
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
    if version not in {LEGACY_SCHEMA_VERSION, SCHEMA_VERSION}:
        errors.append("Unsupported schema version")
    hashes = manifest.get("hashes")
    if not isinstance(hashes, dict):
        return {
            "valid": False,
            "errors": ["Archive hashes manifest is not a mapping"],
            "conflicts": [],
        }
    dataset_specs = _LEGACY_DATASETS if version == LEGACY_SCHEMA_VERSION else _DATASETS
    expected_datasets = {table for table, _, _ in dataset_specs}
    if set(datasets) != expected_datasets:
        errors.append("Archive dataset list does not match schema")
    for table, _, _ in dataset_specs:
        if table not in datasets:
            continue
        actual = f"sha256:{hashlib.sha256(_canonical(datasets[table])).hexdigest()}"
        if hashes.get(table) != actual:
            errors.append(f"Hash mismatch: {table}")
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
    if version == SCHEMA_VERSION:
        source_document_ids.update(
            row["vnb_case_id"]
            for row in dataset_rows("vnb_calculated_values_deliveries")
            if isinstance(row, dict)
            and isinstance(row.get("vnb_case_id"), str)
            and row.get("format_version") == "e66/1"
        )
    period_ids = {
        row.get("id")
        for row in dataset_rows("billing_periods")
        if isinstance(row, dict)
    }
    invoice_ids = {
        row.get("id") for row in dataset_rows("invoices") if isinstance(row, dict)
    }
    for table, _, _ in dataset_specs:
        records = datasets.get(table)
        if not isinstance(records, list) or any(
            not isinstance(row, dict) for row in records
        ):
            errors.append(f"Invalid dataset: {table}")
            continue
        for row in records:
            if "community_id" in row and row["community_id"] != community_id:
                errors.append(f"Community scope violation: {table}")
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
    if not errors:
        try:
            decoded = (
                store.decode_records(datasets)
                if hasattr(store, "decode_records")
                else _python_value(datasets)
            )
        except (ValueError, TypeError):
            errors.append("Invalid tagged value")
    if not errors:
        errors.extend(_validate_vnb_evidence(decoded))
        errors.extend(_validate_operating_relations(decoded))

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
_LEGACY_DATASETS = (
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


# An accepted source belongs to the LEG only while its tenant matches the
# administrator's tenant. Refused intake may contain unassigned private data.
_OWNED_DELIVERY_IDS = """SELECT d.id FROM vnb_calculated_values_deliveries d
    JOIN communities c ON c.community_id = d.community_id
    JOIN buildings b ON b.building_id = c.admin_building_id
    WHERE d.community_id = %s AND d.territory = b.city_id
      AND d.status = 'accepted'"""
_DATASETS = tuple(
    (
        table,
        predicate
        + (
            " OR document_id IN (SELECT d.vnb_case_id "
            "FROM vnb_calculated_values_deliveries d WHERE d.id IN ("
            + _OWNED_DELIVERY_IDS
            + ") AND d.format_version = 'e66/1')"
            if table == "sdat_imports"
            else ""
        ),
        scope,
    )
    for table, predicate, scope in _LEGACY_DATASETS
) + (
    (
        "vnb_calculated_values_deliveries",
        "id IN (" + _OWNED_DELIVERY_IDS + ")",
        "community_id",
    ),
    (
        "vnb_calculated_values_events",
        "delivery_id IN (" + _OWNED_DELIVERY_IDS + ")",
        "community_id",
    ),
)


_DATASETS += (
    ("vnb_submission_cases", "community_id = %s", "community_id"),
    ("vnb_mutation_cases", "community_id = %s", "community_id"),
    (
        "vnb_mutation_events",
        "case_id IN (SELECT case_id FROM vnb_mutation_cases WHERE community_id = %s)",
        "community_id",
    ),
)


_DATASETS += (
    ("bank_statement_imports", "community_id = %s", "community_id"),
    ("bank_statement_entries", "community_id = %s", "community_id"),
    ("invoice_queries", "community_id = %s", "community_id"),
    (
        "invoice_query_messages",
        "query_id IN (SELECT id FROM invoice_queries WHERE community_id = %s)",
        "community_id",
    ),
    (
        "invoice_query_events",
        "query_id IN (SELECT id FROM invoice_queries WHERE community_id = %s)",
        "community_id",
    ),
    ("community_role_events", "community_id = %s", "community_id"),
    ("community_batteries", "community_id = %s", "community_id"),
    ("community_battery_shares", "community_id = %s", "community_id"),
    ("operator_events", "community_id = %s", "community_id"),
    (
        "operator_action_idempotency",
        (
            "community_id = %s AND response IS NOT NULL AND ("
            "action IN (SELECT 'case.respond:' || q.id::text FROM invoice_queries q "
            "WHERE q.community_id = operator_action_idempotency.community_id) OR "
            "action IN (SELECT 'payment.confirm:' || e.id::text FROM bank_statement_entries e "
            "WHERE e.community_id = operator_action_idempotency.community_id))"
        ),
        "community_id",
    ),
)


_VNB_IDENTIFIERS = {
    "vnb_calculated_values_deliveries": {"id": (int,), "vnb_case_id": (str,)},
    "vnb_calculated_values_events": {"id": (int,), "delivery_id": (int,)},
    "vnb_submission_cases": {"case_id": (str,)},
    "vnb_mutation_cases": {"case_id": (str,)},
    "vnb_mutation_events": {"id": (int,), "case_id": (str,)},
}
_OPERATING_IDENTIFIERS = {
    "bank_statement_imports": {"id": (int,)},
    "bank_statement_entries": {
        "id": (int,),
        "statement_import_id": (int,),
        "invoice_id": (int, type(None)),
    },
    "invoice_queries": {"id": (int,), "invoice_id": (int,), "participant_id": (str,)},
    "invoice_query_messages": {"id": (int,), "query_id": (int,), "actor_id": (str,)},
    "invoice_query_events": {"id": (int,), "query_id": (int,), "actor_id": (str,)},
    "community_role_events": {
        "id": (int,),
        "building_id": (str,),
        "actor_building_id": (str, type(None)),
    },
    "community_batteries": {"participant_id": (str, type(None))},
    "community_battery_shares": {"building_id": (str,)},
    "operator_events": {"event_id": (str,), "aggregate_id": (str,)},
    "operator_action_idempotency": {"action": (str,), "idempotency_key": (str,)},
}


def _invalid_relationship_identifiers(datasets, fields):
    for table, columns in fields.items():
        for row in datasets.get(table, []):
            if not isinstance(row, dict) or any(
                type(row.get(column)) not in types for column, types in columns.items()
            ):
                return ["Invalid archive relationship identifier"]
    return []


def _validate_vnb_evidence(datasets):
    """Keep accepted source identity and withdrawal ownership intact on import."""
    shape_errors = _invalid_relationship_identifiers(datasets, _VNB_IDENTIFIERS)
    if shape_errors:
        return shape_errors
    deliveries = datasets.get("vnb_calculated_values_deliveries", [])
    events = datasets.get("vnb_calculated_values_events", [])
    mutation_cases = {
        row.get("case_id") for row in datasets.get("vnb_mutation_cases", [])
    }
    mutation_errors = (
        ["VNB mutation event scope violation"]
        if any(
            row.get("case_id") not in mutation_cases
            for row in datasets.get("vnb_mutation_events", [])
        )
        else []
    )
    if not deliveries and not events:
        return mutation_errors
    community = datasets["communities"][0]
    owner = next(
        (
            row
            for row in datasets.get("buildings", [])
            if row.get("building_id") == community.get("admin_building_id")
        ),
        {},
    )
    territory = owner.get("city_id")
    errors = list(mutation_errors)
    by_id = {}
    for row in deliveries:
        delivery_id = row.get("id")
        if (
            type(delivery_id) is not int
            or delivery_id < 1
            or delivery_id in by_id
            or row.get("status") != "accepted"
            or not territory
            or row.get("territory") != territory
            or row.get("community_id") != community.get("community_id")
        ):
            errors.append("VNB source scope violation")
            continue
        by_id[delivery_id] = row
        evidence = row.get("evidence_bytes")
        if not isinstance(evidence, (bytes, bytearray, memoryview)) or (
            hashlib.sha256(evidence).hexdigest() != row.get("evidence_sha256")
        ):
            errors.append("VNB source evidence hash mismatch")
            continue
        if row.get("format_version") == "e66/1":
            errors.extend(_validate_e66_ledger(row, datasets))
    for row in events:
        delivery_id = row.get("delivery_id")
        source = by_id.get(delivery_id) if type(delivery_id) is int else None
        if (
            not source
            or row.get("community_id") != source.get("community_id")
            or row.get("territory") != source.get("territory")
            or row.get("action") != "withdrawn"
        ):
            errors.append("VNB withdrawal scope violation")
    return errors


def _validate_e66_ledger(source, datasets):
    import e66_evidence

    try:
        document = e66_evidence.parse_source(bytes(source["evidence_bytes"]))
    except e66_evidence.E66EvidenceError:
        return ["VNB E66 source identity mismatch"]
    metadata = source.get("source_metadata")
    ledger = next(
        (
            row
            for row in datasets.get("sdat_imports", [])
            if row.get("document_id") == source.get("vnb_case_id")
        ),
        None,
    )
    if (
        not isinstance(metadata, dict)
        or source.get("vnb_case_id") != document["document_id"]
        or source.get("period_start") != document["period_start"]
        or source.get("period_end") != document["period_end"]
        or metadata.get("vnb_community_id") != document["vnb_community_id"]
        or metadata.get("evidence_sha256") != source["evidence_sha256"]
        or not ledger
        or ledger.get("vnb_community_id") != document["vnb_community_id"]
        or ledger.get("period_start") != document["period_start"]
        or ledger.get("period_end") != document["period_end"]
        or ledger.get("document_created_at") != document["document_created_at"]
        or ledger.get("doc_type") != "E66"
    ):
        return ["VNB E66 source or ledger identity mismatch"]
    return []


def _validate_operating_relations(datasets):
    """Reject foreign parent references before export or restore can disclose them."""
    shape_errors = _invalid_relationship_identifiers(datasets, _OPERATING_IDENTIFIERS)
    if shape_errors:
        return shape_errors
    errors = []
    invoices = {row.get("id"): row for row in datasets.get("invoices", [])}
    queries = {row.get("id") for row in datasets.get("invoice_queries", [])}
    imports = {row.get("id") for row in datasets.get("bank_statement_imports", [])}
    members = {row.get("building_id") for row in datasets.get("community_members", [])}
    for row in datasets.get("invoice_queries", []):
        invoice = invoices.get(row.get("invoice_id"))
        if not invoice or row.get("participant_id") != invoice.get("participant_id"):
            errors.append("Invoice question scope violation")
    for table in ("invoice_query_messages", "invoice_query_events"):
        if any(row.get("query_id") not in queries for row in datasets.get(table, [])):
            errors.append("Invoice question history scope violation")
    if any(
        row.get("statement_import_id") not in imports
        or (row.get("invoice_id") is not None and row.get("invoice_id") not in invoices)
        for row in datasets.get("bank_statement_entries", [])
    ):
        errors.append("Payment reconciliation scope violation")
    if any(
        row.get("building_id") not in members
        or (
            row.get("actor_building_id") is not None
            and row.get("actor_building_id") not in members
        )
        for row in datasets.get("community_role_events", [])
    ):
        errors.append("Role history scope violation")
    if any(
        row.get("building_id") not in members
        for row in datasets.get("community_battery_shares", [])
    ):
        errors.append("Battery share scope violation")
    payment_ids = {row["id"] for row in datasets.get("bank_statement_entries", [])}
    event_ids = {row["event_id"] for row in datasets.get("operator_events", [])}
    portable = {"case.respond": queries, "payment.confirm": payment_ids}
    for row in datasets.get("operator_action_idempotency", []):
        kind, separator, identifier = row["action"].partition(":")
        try:
            parent_id = int(identifier)
        except (ValueError, OverflowError):
            parent_id = None
        response = row.get("response")
        if (
            not separator
            or kind not in portable
            or parent_id not in portable.get(kind, ())
            or identifier != str(parent_id)
            or not isinstance(response, dict)
            or type(response.get("id")) is not int
            or response["id"] != parent_id
            or not isinstance(response.get("event_id"), str)
            or response["event_id"] not in event_ids
        ):
            errors.append("Operator replay scope violation")
    return errors


class PostgresArchiveStore:
    """PostgreSQL adapter for the community archive interface."""

    @staticmethod
    def _connection():
        import database

        return database.get_connection()

    def export_community(self, community_id):
        result = {}
        with self._connection() as conn, conn.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            for table, predicate, _ in _DATASETS:
                query = f"SELECT * FROM {table} WHERE {predicate}"
                cur.execute(
                    query.replace("%s", "%(community_id)s"),
                    {"community_id": community_id},
                )
                result[table] = [dict(row) for row in cur.fetchall()]
        return result if result["communities"] else None

    def find_conflicts(self, community_id, datasets):
        decoded = self.decode_records(datasets)
        conflicts = []
        with self._connection() as conn, conn.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            primary_keys = self._primary_keys(cur)
            for table, _, _ in _DATASETS:
                keys = primary_keys.get(table, [])
                if not keys:
                    continue
                rows = iter(
                    row
                    for row in decoded.get(table, [])
                    if all(key in row for key in keys)
                )
                query = sql.SQL("SELECT * FROM {} WHERE ({}) IN %s").format(
                    sql.Identifier(table),
                    sql.SQL(", ").join(sql.Identifier(key) for key in keys),
                )
                while batch := list(islice(rows, 500)):
                    identities = tuple(tuple(row[key] for key in keys) for row in batch)
                    cur.execute(query, (identities,))
                    existing = {
                        tuple(row[key] for key in keys): row for row in cur.fetchall()
                    }
                    if any(
                        identity in existing
                        and not self._same_record(existing[identity], row, tuple(row))
                        for identity, row in zip(identities, batch)
                    ):
                        conflicts.append(f"Conflicting record: {table}")
                        break
        return conflicts

    @staticmethod
    def _primary_keys(cur):
        cur.execute(
            "SELECT tc.table_name, kcu.column_name "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "ON tc.constraint_name = kcu.constraint_name "
            "AND tc.table_schema = kcu.table_schema "
            "AND tc.table_name = kcu.table_name "
            "WHERE tc.table_schema = 'public' "
            "AND tc.constraint_type = 'PRIMARY KEY' "
            "ORDER BY kcu.ordinal_position"
        )
        result = {}
        for record in cur.fetchall():
            result.setdefault(record["table_name"], []).append(record["column_name"])
        return result

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

    def decode_records(self, datasets):
        """Decode archive scalar tags without interpreting literal JSON objects."""
        with self._connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND data_type IN ('json', 'jsonb')"
            )
            json_columns = {
                (row["table_name"], row["column_name"]) for row in cur.fetchall()
            }
        return {
            table: [
                {
                    column: value
                    if (table, column) in json_columns
                    else _python_value(value)
                    for column, value in row.items()
                }
                for row in rows
            ]
            for table, rows in datasets.items()
        }

    def validate_records(self, datasets):
        with self._connection() as conn, conn.cursor() as cur:
            allowed = self._allowed_columns(cur)
            sequences = self._serial_sequences(
                cur, [table for table, _, _ in _DATASETS if datasets.get(table)]
            )
        errors = []
        if any(row["seqincrement"] < 1 or row["seqcache"] != 1 for row in sequences):
            errors.append("Restore requires ascending sequences with CACHE 1")
        for table, _, _ in _DATASETS:
            rows = datasets.get(table, [])
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                unknown = set(row) - allowed.get(table, set())
                if unknown:
                    errors.append(f"Unknown columns in {table}")
        return errors

    def restore_community(self, datasets):
        with self._connection() as conn, conn.cursor() as cur:
            allowed_columns = self._allowed_columns(cur)
            cur.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND data_type IN ('json', 'jsonb')"
            )
            json_columns = {
                (row["table_name"], row["column_name"]) for row in cur.fetchall()
            }
            primary_keys = self._primary_keys(cur)
            sequences = self._serial_sequences(
                cur, [table for table, _, _ in _DATASETS if datasets.get(table)]
            )
            # Lock before inserting explicit IDs. Otherwise a writer can reserve
            # the same ID and wait on our row while we wait on its sequence lock.
            for sequence in sequences:
                if sequence["seqincrement"] < 1 or sequence["seqcache"] != 1:
                    raise ArchiveError(
                        "Restore requires ascending sequences with CACHE 1"
                    )
                cur.execute(
                    sql.SQL("ALTER SEQUENCE {} INCREMENT BY {}").format(
                        sql.Identifier(sequence["nspname"], sequence["relname"]),
                        sql.Literal(sequence["seqincrement"]),
                    )
                )
            for table, _, _ in _DATASETS:
                rows = datasets.get(table, [])
                if (
                    table in {"meter_readings", "metering_point_readings"}
                    and primary_keys.get(table) == ["id"]
                    and rows
                    and all(isinstance(row, dict) and "id" in row for row in rows)
                ):
                    self._restore_readings(
                        cur, table, rows, allowed_columns[table], json_columns
                    )
                    continue
                for row in rows:
                    if not isinstance(row, dict) or not row:
                        raise ArchiveError(f"Invalid record: {table}")
                    # Column names are always derived from the database
                    # catalog, never from the archive; the row may only use
                    # known names and every unknown key is rejected.
                    catalog_columns = allowed_columns.get(table, frozenset())
                    if not set(row) <= catalog_columns:
                        raise ArchiveError(f"Unknown column: {table}")
                    columns = tuple(
                        column for column in sorted(catalog_columns) if column in row
                    )
                    placeholders = ", ".join(["%s"] * len(columns))
                    cur.execute(
                        f"INSERT INTO {table} ({', '.join(columns)}) "
                        f"VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                        tuple(
                            Json(row[column])
                            if (table, column) in json_columns
                            and row[column] is not None
                            else row[column]
                            for column in columns
                        ),
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
                        if not self._same_record(existing, row, columns):
                            raise ArchiveError(f"Conflicting record: {table}")
            self._advance_sequences(cur, sequences)

    @staticmethod
    def _same_record(existing, row, columns):
        return existing is not None and all(
            (
                bytes(existing[column])
                if isinstance(existing[column], memoryview)
                else existing[column]
            )
            == row[column]
            for column in columns
        )

    def _restore_readings(self, cur, table, rows, catalog_columns, json_columns):
        """Bound database round trips while retaining every row conflict check."""
        for row in rows:
            if not set(row) <= catalog_columns:
                raise ArchiveError(f"Unknown column: {table}")
        for columns, group in groupby(
            rows,
            key=lambda row: tuple(
                column for column in sorted(catalog_columns) if column in row
            ),
        ):
            while batch := list(islice(group, 500)):
                inserted = execute_values(
                    cur,
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s "
                    "ON CONFLICT DO NOTHING RETURNING *",
                    [
                        tuple(
                            Json(row[column])
                            if (table, column) in json_columns
                            and row[column] is not None
                            else row[column]
                            for column in columns
                        )
                        for row in batch
                    ],
                    page_size=500,
                    fetch=True,
                )
                stored = {row["id"]: row for row in inserted}
                missing = [row["id"] for row in batch if row["id"] not in stored]
                if missing:
                    cur.execute(f"SELECT * FROM {table} WHERE id = ANY(%s)", (missing,))
                    stored.update((row["id"], row) for row in cur.fetchall())
                if any(
                    not self._same_record(stored.get(row["id"]), row, columns)
                    for row in batch
                ):
                    raise ArchiveError(f"Conflicting record: {table}")

    @staticmethod
    def _serial_sequences(cur, tables):
        cur.execute(
            "SELECT t.relname AS table_name, a.attname AS column_name, "
            "n.nspname, c.relname, s.seqincrement, s.seqcache "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_sequence s ON s.seqrelid = c.oid "
            "JOIN pg_depend d ON d.classid = 'pg_class'::regclass "
            "AND d.objid = c.oid AND d.deptype IN ('a', 'i') "
            "JOIN pg_class t ON t.oid = d.refobjid "
            "JOIN pg_namespace tn ON tn.oid = t.relnamespace "
            "JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid "
            "WHERE tn.nspname = 'public' AND t.relname = ANY(%s) "
            "ORDER BY t.relname, a.attname",
            (tables,),
        )
        return cur.fetchall()

    @staticmethod
    def _advance_sequences(cur, sequences):
        """Keep restored serial IDs and future writes compatible, transactionally."""
        for sequence in sequences:
            table, column = sequence["table_name"], sequence["column_name"]
            increment = sequence["seqincrement"]
            identifier = sql.Identifier(sequence["nspname"], sequence["relname"])
            cur.execute(
                sql.SQL("SELECT last_value, is_called FROM {}").format(identifier)
            )
            position = cur.fetchone()
            cur.execute(
                sql.SQL("SELECT MAX({}) AS maximum FROM {}").format(
                    sql.Identifier(column), sql.Identifier(table)
                )
            )
            maximum = cur.fetchone()["maximum"]
            next_value = max(
                position["last_value"] + (increment if position["is_called"] else 0),
                (maximum + increment)
                if maximum is not None
                else position["last_value"],
            )
            cur.execute(
                sql.SQL("ALTER SEQUENCE {} RESTART WITH {}").format(
                    identifier, sql.Literal(next_value)
                )
            )
