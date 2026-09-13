# SPDX-License-Identifier: AGPL-3.0-or-later
"""Operator totals are independent of the displayed record limit."""

import pytest

import database as db
from tests import test_interest_confirmation_routes, test_interest_postgres

interest_database = test_interest_postgres.interest_database
interest_client = test_interest_confirmation_routes.interest_client


@pytest.mark.integration
def test_operator_counts_all_records_beyond_the_display_limit(
    interest_client, monkeypatch
):
    client, _tasks, _cluster = interest_client
    monkeypatch.setenv("ADMIN_TOKEN", "test-operator-token")
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO coverage_requests
            (request_id, email, address, plz, municipality_name, bfs_number, verified, token_expires_at)
            SELECT n::text, n::text || '@example.ch', '', '4533', 'Riedholz', 2554,
                   n <= 501, NOW() + INTERVAL '30 days'
            FROM generate_series(1, 504) n""")
    assert test_interest_postgres.save_registration(verified=True)
    response = client.get(
        "/admin/ops", headers={"X-Admin-Token": "test-operator-token"}
    )
    assert response.status_code == 200
    assert len(response.json["interest_records"]) == 500
    assert response.json["counts"]["interest_verified"] == 502
    assert response.json["counts"]["interest_unverified"] == 3
