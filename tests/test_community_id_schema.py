# SPDX-License-Identifier: AGPL-3.0-or-later
"""Schema contract: community_id is joinable across community tables.

communities.community_id is VARCHAR(64) (a UUID string). billing_periods and
invoices historically declared community_id as INTEGER, which made the join
to communities impossible. This contract pins the aligned types and the
idempotent migration for pre-existing databases.
"""

import os
import re

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _database_source() -> str:
    with open(os.path.join(PROJECT_ROOT, "database.py"), encoding="utf-8") as handle:
        return handle.read()


def _create_block(source: str, table: str) -> str:
    match = re.search(
        rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\)\s*\"\"\"",
        source,
        flags=re.DOTALL,
    )
    assert match, f"CREATE TABLE {table} block not found"
    return match.group(1)


def test_billing_periods_community_id_is_varchar():
    block = _create_block(_database_source(), "billing_periods")
    assert re.search(r"community_id VARCHAR\(64\)", block), (
        "billing_periods.community_id must be VARCHAR(64) to join communities"
    )
    assert "community_id INTEGER" not in block


def test_invoices_community_id_is_varchar():
    block = _create_block(_database_source(), "invoices")
    assert re.search(r"community_id VARCHAR\(64\)", block), (
        "invoices.community_id must be VARCHAR(64) to join communities"
    )
    assert "community_id INTEGER" not in block


def test_leg_documents_community_id_is_varchar():
    block = _create_block(_database_source(), "leg_documents")
    assert re.search(r"community_id VARCHAR\(64\)", block), (
        "leg_documents.community_id must be VARCHAR(64) to join communities"
    )
    assert "community_id INTEGER" not in block


def test_migration_converts_existing_integer_columns():
    source = _database_source()
    for table in ("billing_periods", "invoices", "leg_documents"):
        assert re.search(
            rf"ALTER TABLE {table}\s+ALTER COLUMN community_id TYPE VARCHAR\(64\)",
            source,
        ), f"idempotent migration for {table}.community_id missing"


def test_store_billing_type_hint_matches_schema():
    with open(
        os.path.join(PROJECT_ROOT, "store", "billing.py"), encoding="utf-8"
    ) as handle:
        source = handle.read()
    assert "community_id: int" not in source, (
        "store/billing.py must not annotate community_id as int; the schema "
        "uses VARCHAR(64) UUID strings"
    )
