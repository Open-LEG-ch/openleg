# SPDX-License-Identifier: AGPL-3.0-or-later
"""Token lookup failures cannot bypass pre-deletion cookie revocation."""

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
