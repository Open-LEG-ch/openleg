# SPDX-License-Identifier: AGPL-3.0-or-later
"""Utility login uses the shared one-time-token policy and clean sessions."""

import re
from urllib.parse import urlsplit

import pytest

from tests import security_review_fixtures
from tests import test_interest_postgres as interest_postgres_tests

app_module = security_review_fixtures.app_module
interest_database = interest_postgres_tests.interest_database

pytestmark = pytest.mark.integration


def test_utility_links_store_only_digests_and_reset_the_session(
    app_module, interest_database, monkeypatch
):
    import utility_portal

    db = interest_database
    assert db.save_utility_client("synthetic-grid", "Synthetic grid", "grid@example.ch")
    mails = []
    monkeypatch.setattr(
        utility_portal,
        "send_email",
        lambda to, subject, body, **kwargs: mails.append(body) or True,
    )
    client = app_module.web.test_client()
    response = client.post("/utility/login", json={"email": "grid@example.ch"})
    assert response.status_code == 200
    link = re.search(r"https?://\S+", mails[-1]).group()
    path = urlsplit(link).path
    assert path.startswith("/utility/access/")
    assert "token=" not in urlsplit(link).query
    raw_token = path.rsplit("/", 1)[1]
    stored = db.get_utility_client("synthetic-grid")
    digest = stored.get("magic_link_token_hash")
    assert digest and len(digest) == 64 and digest != raw_token
    assert stored["magic_link_token"] is None
    with client.session_transaction() as state:
        state["dashboard_building_id"] = "other-household"
        state["utility_api_csrf"] = "stale-csrf"
        state["unrelated"] = "stale"
    accepted = client.get(path)
    assert accepted.status_code == 302
    assert accepted.headers["Location"].endswith("/utility/dashboard")
    assert accepted.headers["Cache-Control"] == "no-store"
    with client.session_transaction() as state:
        assert state["utility_client_id"] == "synthetic-grid"
        assert "dashboard_building_id" not in state
        assert "unrelated" not in state
        assert state.get("utility_api_csrf") != "stale-csrf"
    for rejected_path in (
        path,
        "/utility/access/" + digest,
        "/utility/login?token=" + raw_token,
    ):
        other = app_module.web.test_client()
        other.get(rejected_path)
        with other.session_transaction() as state:
            assert "utility_client_id" not in state

    page = client.get("/utility/dashboard")
    assert '<form method="post" action="/utility/logout"' in page.text
    with client.session_transaction() as state:
        csrf = state["utility_api_csrf"]
    assert client.get("/utility/logout").status_code == 405
    assert client.post("/utility/logout").status_code == 403
    assert client.post("/utility/logout", data={"csrf_token": csrf}).status_code == 302
    with client.session_transaction() as state:
        assert "utility_client_id" not in state
