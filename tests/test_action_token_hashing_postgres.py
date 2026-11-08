# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bearer action links work while their database values cannot be replayed."""

import uuid

import pytest

from tests import test_interest_postgres as interest_postgres_tests

interest_database = interest_postgres_tests.interest_database

pytestmark = pytest.mark.integration


def _building(db, **kwargs):
    assert db.save_building(
        "synthetic",
        "owner@example.ch",
        {"address": "Testweg 1", "lat": 47.2, "lon": 8.2},
        {},
        **kwargs,
    )


def _stored(db, query):
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(query)
        return next(iter(cur.fetchone().values()))


def test_building_confirmation_digest_cannot_be_used_as_a_bearer(interest_database):
    db = interest_database
    raw = str(uuid.uuid4())
    _building(db, verification_token=raw)
    digest = _stored(db, "SELECT token FROM tokens")
    assert digest != raw
    assert db.confirm_building_interest(digest) is None
    assert db.confirm_building_interest(raw)["verified"] is True
    assert db.confirm_building_interest(raw) is None


def test_unsubscribe_digest_cannot_delete_a_profile(interest_database):
    db = interest_database
    _building(db)
    raw = str(uuid.uuid4())
    assert db.save_token(raw, "synthetic", "unsubscribe")
    digest = _stored(db, "SELECT token FROM tokens")
    assert digest != raw
    assert db.confirm_profile_deletion(digest) is False
    assert db.get_building("synthetic") is not None
    assert db.confirm_profile_deletion(raw) is True
    assert db.confirm_profile_deletion(raw) is False


def test_coverage_confirmation_stores_only_a_digest(interest_database):
    db = interest_database
    raw = str(uuid.uuid4())
    assert db.save_coverage_request(
        request_id="synthetic-coverage",
        email="owner@example.ch",
        address="Testweg 1",
        plz="8000",
        municipality_name="Synthetic",
        canton="ZH",
        bfs_number=261,
        roles=["resident"],
        has_solar=False,
        verification_token=raw,
    )
    digest = _stored(db, "SELECT verification_token FROM coverage_requests")
    assert digest != raw
    assert db.verify_coverage_request(digest) is None
    assert db.verify_coverage_request(raw)["bfs_number"] == 261
    assert db.verify_coverage_request(raw) is None


@pytest.mark.parametrize("kind", ["claim", "verification"])
def test_registry_link_digests_do_not_grant_access(interest_database, kind):
    db = interest_database
    entry = db.save_registry_entry("synthetic", "Synthetic", "owner@example.ch")
    raw = str(uuid.uuid4())
    assert getattr(db, f"set_registry_{kind}_token")(entry["id"], raw)
    digest = _stored(db, f"SELECT {kind}_token FROM leg_registry")
    assert digest != raw
    assert getattr(db, f"get_registry_entry_by_{kind}_token")(digest) is None
    assert getattr(db, f"get_registry_entry_by_{kind}_token")(raw)["id"] == entry["id"]


def test_schema_upgrade_hashes_existing_links_once_without_breaking_them(
    interest_database,
):
    db = interest_database
    _building(db)
    raw = str(uuid.uuid4())
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tokens(token,building_id,token_type,expires_at) VALUES (%s,'synthetic','unsubscribe',CURRENT_TIMESTAMP + INTERVAL '1 hour')",
            (raw,),
        )
    db.create_tables()
    digest = _stored(db, "SELECT token FROM tokens")
    assert digest != raw
    db.create_tables()
    assert _stored(db, "SELECT token FROM tokens") == digest
    assert db.get_token(digest) is None
    assert db.get_token(raw)["building_id"] == "synthetic"
