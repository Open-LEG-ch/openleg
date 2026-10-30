# SPDX-License-Identifier: AGPL-3.0-or-later
"""Persistence contracts for verified municipality interest."""

from unittest.mock import MagicMock, patch

from store import interest


def _connection_with_cursor(*, rows=None, row=None):
    cursor = MagicMock()
    cursor.fetchall.return_value = rows or []
    cursor.fetchone.return_value = row
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    return connection, cursor


def test_retention_horizons_are_named_and_used():
    connection, cursor = _connection_with_cursor()
    cursor.rowcount = 2
    with patch("store.interest._get_connection", return_value=connection):
        result = interest.cleanup_expired_interest()

    sql = " ".join(call.args[0] for call in cursor.execute.call_args_list)
    assert interest.UNVERIFIED_INTEREST_RETENTION_DAYS == 30
    assert interest.VERIFIED_COVERAGE_RETENTION_MONTHS == 12
    assert "30 days" in sql
    assert "12 months" in sql
    assert "NOT EXISTS (SELECT 1 FROM communities" in sql
    assert result == {"coverage_requests_deleted": 2, "buildings_deleted": 2}


def test_operator_interest_records_unifies_both_intake_paths():
    rows = [{"source": "address_check", "email": "one@example.ch"}]
    connection, cursor = _connection_with_cursor(rows=rows)
    with patch("store.interest._get_connection", return_value=connection):
        result = interest.get_operator_interest_records(limit=25)

    sql = cursor.execute.call_args.args[0]
    assert "FROM buildings" in sql
    assert "FROM coverage_requests" in sql
    assert cursor.execute.call_args.args[1] == (25,)
    assert result == rows


def test_municipality_summary_deduplicates_people_across_intake_paths():
    connection, cursor = _connection_with_cursor(
        row={"verified_total": 1}, rows=[{"role": "owner", "count": 1}]
    )
    with patch("store.interest._get_connection", return_value=connection):
        result = interest.get_municipality_interest_summary(2554)

    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert len(statements) == 2
    assert all("FROM verified_interest" in sql for sql in statements)
    assert all("bfs_number = %s" in sql for sql in statements)
    assert all(call.args[1] == (2554,) for call in cursor.execute.call_args_list)
    assert result["verified_total"] == 1
    assert result["roles"] == {"owner": 1}
