# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deletion validation and transaction failures preserve bound logins."""

from uuid import uuid4

import pytest

from sandbox_sessions import VillageSessions
from tests import test_community_create_postgres
from tests.test_dashboard_access_routes import _set_session

interest_database = test_community_create_postgres.interest_database
founder = test_community_create_postgres.founder
pytestmark = pytest.mark.integration


def test_failed_token_precheck_cannot_recover_into_an_unrevoked_deletion(
    founder, tmp_path, monkeypatch
):
    client, db = founder
    binding = VillageSessions(tmp_path)
    binding.install(client.application)
    _set_session(client, "founder")
    previous = binding._current()
    with client.session_transaction() as state:
        state["sandbox_generation"] = previous
    token = str(uuid4())
    assert db.save_token(token, "founder", "unsubscribe")
    lookup = db.get_token
    calls = []

    def transient_failure(value):
        calls.append(value)
        return None if len(calls) == 1 else lookup(value)

    monkeypatch.setattr(db, "get_token", transient_failure)
    assert client.post("/unsubscribe/" + token).status_code == 404
    assert calls == [token]
    with client.session_transaction() as state:
        assert state["dashboard_building_id"] == "founder"
    assert db.get_building("founder")["verified"] is True
    assert binding._current() == previous
    assert lookup(token) is not None


def test_token_expiring_after_route_lookup_preserves_profile_and_login(
    founder, tmp_path, monkeypatch
):
    client, db = founder
    binding = VillageSessions(tmp_path)
    binding.install(client.application)
    _set_session(client, "founder")
    previous = binding._current()
    with client.session_transaction() as state:
        state["sandbox_generation"] = previous
    token = str(uuid4())
    assert db.save_token(token, "founder", "unsubscribe")
    lookup = db.get_token

    def expire_after_lookup(value):
        record = lookup(value)
        with db.get_connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE tokens SET expires_at=CURRENT_TIMESTAMP-INTERVAL '1 hour' WHERE token=%s",
                (value,),
            )
        return record

    monkeypatch.setattr(db, "get_token", expire_after_lookup)
    assert client.post("/unsubscribe/" + token).status_code in (404, 409)
    assert binding._current() == previous
    assert db.get_building("founder")["verified"] is True
    with client.session_transaction() as state:
        assert state["dashboard_building_id"] == "founder"


def test_head_confirmation_never_deletes_profile_or_rotates_login(founder, tmp_path):
    client, db = founder
    binding = VillageSessions(tmp_path)
    binding.install(client.application)
    _set_session(client, "founder")
    previous = binding._current()
    with client.session_transaction() as state:
        state["sandbox_generation"] = previous
    token = str(uuid4())
    assert db.save_token(token, "founder", "unsubscribe")
    response = client.head("/unsubscribe/" + token)
    assert response.status_code == 200 and response.data == b""
    assert db.get_building("founder")["verified"] is True
    assert db.get_token(token) is not None
    assert binding._current() == previous


def test_generation_failure_after_delete_rolls_back_profile_and_token(
    founder, tmp_path, monkeypatch
):
    client, db = founder
    binding = VillageSessions(tmp_path)
    binding.install(client.application)
    _set_session(client, "founder")
    previous = binding._current()
    with client.session_transaction() as state:
        state["sandbox_generation"] = previous
    token = str(uuid4())
    assert db.save_token(token, "founder", "unsubscribe")
    with db.get_connection() as connection, connection.cursor() as cursor:
        cursor.execute("CREATE SEQUENCE sandbox_deletion_attempt")
        cursor.execute("""CREATE FUNCTION sandbox_note_deletion() RETURNS trigger LANGUAGE plpgsql AS $$
                       BEGIN PERFORM nextval('sandbox_deletion_attempt'); RETURN OLD; END $$""")
        cursor.execute("""CREATE TRIGGER sandbox_note_deletion BEFORE DELETE ON buildings
                       FOR EACH ROW EXECUTE FUNCTION sandbox_note_deletion()""")

    def unavailable():
        raise OSError("synthetic generation storage failure")

    rotate = binding._rotate
    monkeypatch.setattr(binding, "_rotate", unavailable)
    try:
        assert client.post("/unsubscribe/" + token).status_code == 503
        with db.get_connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT is_called FROM sandbox_deletion_attempt")
            assert cursor.fetchone()["is_called"] is True
        assert db.get_building("founder")["verified"] is True
        assert db.get_token(token) is not None
        assert binding._current() == previous
        monkeypatch.setattr(binding, "_rotate", rotate)
        assert client.post("/unsubscribe/" + token).status_code == 200
        assert db.get_building("founder") is None
        assert db.get_token(token) is None
        assert binding._current() != previous
    finally:
        with db.get_connection() as connection, connection.cursor() as cursor:
            cursor.execute("DROP TRIGGER sandbox_note_deletion ON buildings")
            cursor.execute("DROP FUNCTION sandbox_note_deletion()")
            cursor.execute("DROP SEQUENCE sandbox_deletion_attempt")
