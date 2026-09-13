# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verification revisions, migration and expiry during a database lock wait."""

import uuid

import pytest

import database as db
from tests import test_interest_postgres
from tests.test_interest_postgres import (
    save_registration,
    transaction_race,
)

interest_database = test_interest_postgres.interest_database


@pytest.mark.integration
def test_returning_to_an_old_email_does_not_revive_its_link(interest_database):
    original, replacement, current = [str(uuid.uuid4()) for _ in range(3)]
    assert save_registration(verification_token=original)
    assert save_registration("new@example.ch", verification_token=replacement)
    assert save_registration(verification_token=current)
    assert db.confirm_building_interest(original) is None
    assert db.confirm_building_interest(replacement) is None
    assert db.confirm_building_interest(current)["email"] == "one@example.ch"


@pytest.mark.integration
def test_legacy_migration_rejects_unbound_verification_but_keeps_unsubscribe(
    interest_database,
):
    assert save_registration()
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("ALTER TABLE tokens DROP COLUMN verification_revision")
        cur.execute("ALTER TABLE buildings DROP COLUMN verification_revision")
    assert db.save_token("legacy-confirm", "interest-building", "verification")
    assert db.save_token("legacy-unsubscribe", "interest-building", "unsubscribe")

    db.create_tables()
    db.create_tables()

    assert db.confirm_building_interest("legacy-confirm") is None
    assert db.get_token("legacy-confirm") is None
    assert db.get_token("legacy-unsubscribe")["token_type"] == "unsubscribe"
    assert db.get_building("interest-building")["verified"] is False


@pytest.mark.integration
@pytest.mark.parametrize("token_type", ["verification", "unsubscribe"])
def test_token_collision_cannot_rebind_an_existing_link(interest_database, token_type):
    token = str(uuid.uuid4())
    assert save_registration(
        verification_token=token if token_type == "verification" else None
    )
    if token_type == "unsubscribe":
        assert db.save_token(token, "interest-building", token_type)
    assert save_registration("new@example.ch", verification_token=token) is False
    assert db.get_building("interest-building")["email"] == "one@example.ch"
    assert db.get_token(token)["token_type"] == token_type


@pytest.mark.integration
def test_link_expiring_while_waiting_for_building_lock_is_rejected(
    interest_database,
    monkeypatch,
):
    token = str(uuid.uuid4())
    assert save_registration(verification_token=token)
    with transaction_race(monkeypatch) as race:
        registration = race.start(save_registration, pause_before_commit=True)
        race.wait_until_staged(registration)
        confirmation = race.start(lambda: db.confirm_building_interest(token))
        race.wait_until_blocked_by(confirmation, registration)
        with race.original_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE tokens SET expires_at = clock_timestamp() WHERE token = %s",
                (token,),
            )
        registration.release.set()
        assert race.finish(registration) is True
        assert race.finish(confirmation) is None
    assert db.get_building("interest-building")["verified"] is False
