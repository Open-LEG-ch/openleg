# SPDX-License-Identifier: AGPL-3.0-or-later
"""Malformed relationship identifiers report errors before any archive writes."""

import hashlib
import json

import pytest

import community_archive
from tests.test_community_archive import MemoryArchiveStore, representative_data


@pytest.mark.parametrize(
    "table,row,column,value",
    [
        (
            "vnb_mutation_cases",
            {"case_id": "mutation-1", "community_id": "leg-1"},
            "case_id",
            [],
        ),
        ("vnb_mutation_events", {"id": 1, "case_id": "mutation-1"}, "case_id", {}),
        (
            "invoice_queries",
            {
                "id": 1,
                "community_id": "leg-1",
                "invoice_id": 7,
                "participant_id": "house-1",
            },
            "id",
            [],
        ),
        (
            "invoice_query_messages",
            {"id": 1, "query_id": 1, "actor_id": "house-1"},
            "query_id",
            {},
        ),
        (
            "bank_statement_entries",
            {
                "id": 1,
                "community_id": "leg-1",
                "statement_import_id": 1,
                "invoice_id": None,
            },
            "statement_import_id",
            [],
        ),
        (
            "community_role_events",
            {
                "id": 1,
                "community_id": "leg-1",
                "building_id": "house-1",
                "actor_building_id": "house-1",
            },
            "building_id",
            {},
        ),
        (
            "operator_events",
            {"event_id": "event-1", "aggregate_id": "1", "community_id": "leg-1"},
            "event_id",
            [],
        ),
        (
            "operator_action_idempotency",
            {
                "community_id": "leg-1",
                "action": "case.respond:1",
                "idempotency_key": "key",
                "response": {"id": 1},
            },
            "action",
            [],
        ),
    ],
)
def test_non_scalar_operating_identifiers_return_fixed_errors(
    table, row, column, value
):
    original = community_archive.export_community_archive(
        "leg-1", store=MemoryArchiveStore({"leg-1": representative_data()})
    )
    archive = json.loads(original)
    row[column] = value
    archive["datasets"][table] = [row]
    encoded = json.dumps(
        [row], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    archive["manifest"]["hashes"][table] = (
        "sha256:" + hashlib.sha256(encoded).hexdigest()
    )
    target = MemoryArchiveStore()
    result = community_archive.restore_community_archive(
        json.dumps(archive), store=target
    )
    assert result["valid"] is False
    assert result["errors"]
    assert "Invalid archive relationship identifier" in result["errors"]
    assert target.restore_calls == 0
