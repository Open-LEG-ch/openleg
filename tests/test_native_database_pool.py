# SPDX-License-Identifier: AGPL-3.0-or-later
"""A newly installed native pool belongs to this process, even after fork."""

import os
from urllib.parse import urlsplit

import pytest

import database
from tests import test_interest_postgres, test_schema_migrations
from tests import test_store_metering_postgres as metering
from tests import test_store_ops_postgres as ops

interest_database = test_interest_postgres.interest_database
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("DATABASE_URL"), reason="requires PostgreSQL DATABASE_URL"
    ),
]


@pytest.mark.parametrize(
    "pool_context",
    [test_schema_migrations._pool_against, metering._pool_against, ops._pool_against],
    ids=["schema", "metering", "operations"],
)
def test_replacement_pool_uses_its_own_database_after_inherited_process_state(
    monkeypatch, pool_context
):
    monkeypatch.setattr(database, "_pool_pid", os.getpid() + 1)
    with test_schema_migrations._temporary_database() as url, pool_context(url):
        with database.get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT current_database() AS name")
            assert cur.fetchone()["name"] == urlsplit(url).path.lstrip("/")


def test_interest_fixture_uses_its_private_database_after_inherited_process_state(
    request, monkeypatch
):
    monkeypatch.setattr(database, "_pool_pid", os.getpid() + 1)
    db = request.getfixturevalue("interest_database")
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT current_database() AS name")
        assert cur.fetchone()["name"].startswith("openleg_interest_")
