# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each deletion email follows its token-bound registration's saved language."""

import re
from urllib.parse import urlsplit

import pytest

import database as db
from tests import test_interest_confirmation_routes, test_interest_postgres

interest_database = test_interest_postgres.interest_database
interest_client = test_interest_confirmation_routes.interest_client
pytestmark = pytest.mark.integration


def coverage():
    assert db.save_coverage_request(
        request_id="synthetic-coverage",
        email="member@example.com",
        address="Synthetic private address",
        plz="4533",
        municipality_name="Synthetic town",
        canton="SO",
        bfs_number=2554,
        roles=[],
        has_solar=False,
        verification_token="synthetic-confirmation",
        preferred_locale="fr_CH",
    )


def test_deletion_mail_uses_each_record_language_with_one_shared_mailbox(
    interest_client, monkeypatch
):
    client, _tasks, _cluster = interest_client
    assert db.save_building(
        "synthetic-building",
        "member@example.com",
        {"address": "Synthetic private address", "lat": 47.2, "lon": 8.2},
        {},
        verified=True,
        preferred_locale="de_CH",
    )
    coverage()
    import app

    captured = []
    monkeypatch.setattr(app, "send_email", lambda *args: captured.append(args))
    response = client.post(
        "/unsubscribe?lang=fr-CH",
        data={"email": "MEMBER@example.com", "preferred_locale": "fr-CH"},
    )
    assert response.status_code == 200
    assert "member@example.com" not in response.text
    assert len(captured) == 2
    links = []
    for recipient, subject, body in captured:
        assert recipient.casefold() == "member@example.com"
        assert "Synthetic private address" not in body
        link = re.search(r"http[^\s]+/unsubscribe/[^\s]+", body)[0]
        parsed = urlsplit(link)
        token = parsed.path.rsplit("/", 1)[1]
        record = db.get_token(token)
        locale = "fr_CH" if record["coverage_request_id"] else "de_CH"
        assert ("suppression" if locale == "fr_CH" else "Löschung") in subject
        assert "lang=" + locale.replace("_", "-") in parsed.query
        assert client.get(parsed.path + "?" + parsed.query).status_code == 200
        links.append(parsed)
    assert db.get_building("synthetic-building") is not None
    assert len(db.get_operator_interest_records()) == 2
    for parsed in links:
        assert client.post(parsed.path + "?" + parsed.query).status_code == 200
        assert client.post(parsed.path + "?" + parsed.query).status_code == 404
    assert db.get_building("synthetic-building") is None
    assert db.get_operator_interest_records() == []


@pytest.mark.parametrize("failure", ["wrong_email", "expired", "used", "missing"])
def test_deletion_language_is_bound_to_the_active_token_and_mailbox(
    interest_database, failure
):
    coverage()
    (token,) = db.create_coverage_deletion_tokens("member@example.com")
    assert db.get_deletion_recipient_locale(token, " MEMBER@example.com ") == "fr_CH"
    email = "member@example.com"
    if failure == "wrong_email":
        email = "other@example.com"
    elif failure == "missing":
        token = "synthetic-missing-token"
    else:
        with db.get_connection() as connection, connection.cursor() as cursor:
            if failure == "expired":
                cursor.execute(
                    "UPDATE tokens SET expires_at = NOW() - INTERVAL '1 second' WHERE token = %s",
                    (token,),
                )
            else:
                cursor.execute(
                    "UPDATE tokens SET used_at = NOW() WHERE token = %s", (token,)
                )
    assert db.get_deletion_recipient_locale(token, email) is None


def test_deletion_language_lookup_failure_does_not_log_private_values(
    monkeypatch, caplog
):
    def unavailable():
        raise RuntimeError("PRIVATE-SYNTHETIC-TOKEN recipient@example.com")

    monkeypatch.setattr(db, "get_connection", unavailable)
    assert (
        db.get_deletion_recipient_locale("private-token", "member@example.com") is None
    )
    assert "PRIVATE-SYNTHETIC-TOKEN" not in caplog.text
    assert "recipient@example.com" not in caplog.text
