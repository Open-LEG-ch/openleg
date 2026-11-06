# SPDX-License-Identifier: AGPL-3.0-or-later
"""Actual occurrences, private drafting and stable calendar identity."""

import pytest

from tests import test_verein_membership

_accepted = test_verein_membership._accepted
app_module = test_verein_membership.app_module
isolated_environment = test_verein_membership.isolated_environment
membership = test_verein_membership.membership
web = test_verein_membership.web

pytestmark = pytest.mark.integration


def _event(membership):
    import database
    from store import verein_meetups

    client, _mail, store, _reviewer_id, _path = _accepted(membership)
    with database.get_connection() as conn, conn.cursor() as cur:
        verein_meetups.create_tables(cur)
    with client.session_transaction() as session:
        token = session["verein_session"]
    data = {
        "slug": "fictional-march-meetup",
        "start": "2027-03-28T10:00",
        "end": "2027-03-28T11:00",
        "organiser": "Fictional test association",
        "spoken_languages": "de-CH, en",
        "join_state": "unavailable",
        "join_url": "",
        "status": "draft",
        "translations": {
            locale: {
                "title": "Fictional meetup " + locale,
                "audience": "Interested people",
                "agenda": "Questions and next steps",
            }
            for locale in ("de_CH", "en", "fr_CH", "it_CH")
        },
    }
    return client, store, token, data, verein_meetups


def test_private_draft_publishes_and_keeps_identity_when_moved_or_cancelled(membership):
    client, _store, token, data, events = _event(membership)
    identifier = events.save(token, data)
    path = "/veranstaltungen/" + data["slug"]
    assert client.get(path).status_code == 404
    data["status"] = "scheduled"
    events.save(token, data, identifier=identifier, version=1)
    response = client.get(path + "?lang=en")
    assert response.status_code == 200
    assert "Fictional meetup en" in response.text
    assert "Questions and next steps" in response.text
    calendar = client.get(path + ".ics")
    assert calendar.status_code == 200
    assert calendar.mimetype == "text/calendar"
    assert b"DTSTART:20270328T080000Z" in calendar.data
    assert b"SEQUENCE:2" in calendar.data
    assert b"UID:" + identifier.encode() + b"@openleg.ch" in calendar.data
    data.update(start="2027-10-31T10:00", end="2027-10-31T11:00", status="cancelled")
    events.save(token, data, identifier=identifier, version=2)
    cancelled = client.get(path + ".ics")
    assert b"DTSTART:20271031T090000Z" in cancelled.data
    assert b"SEQUENCE:3" in cancelled.data
    assert b"STATUS:CANCELLED" in cancelled.data
    assert b"UID:" + identifier.encode() + b"@openleg.ch" in cancelled.data
    with pytest.raises(events.verein.VereinDenied):
        events.save(token, data, identifier=identifier, version=2)
    assert client.get(path).status_code == 200


def test_event_access_and_notes_require_actual_board_publication(
    membership, monkeypatch
):
    client, store, token, data, events = _event(membership)
    data.update(
        status="scheduled",
        start="2026-01-10T10:00",
        end="2026-01-10T11:00",
        join_state="open",
        join_url="https://example.org/public-registration",
    )
    identifier = events.save(token, data)
    path = "/veranstaltungen/" + data["slug"]
    notes = {
        locale: "Approved plain notes " + locale for locale in data["translations"]
    }
    from psycopg2.extras import Json

    import database

    with database.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE verein_meetups SET notes=%s WHERE id=%s", (Json(notes), identifier)
        )
    assert "Approved plain notes" not in client.get(path).text
    with pytest.raises(events.verein.VereinDenied):
        events.publish_notes(
            "anonymous", identifier, notes, "Actual approval reference", version=1
        )
    events.publish_notes(
        token, identifier, notes, "Board approval 2026-01 item 2", version=1
    )
    with pytest.raises(events.verein.VereinDenied):
        events.publish_notes(token, identifier, notes, "Stale approval", version=1)
    assert "Approved plain notes en" in client.get(path + "?lang=en").text
    calendar = client.get(path + ".ics?lang=en")
    assert b"public-registration" not in calendar.data
    assert b"Approved plain notes" not in calendar.data
    events.withdraw_notes(
        token, identifier, "Correction requested by participant", version=2
    )
    assert "Approved plain notes" not in client.get(path).text
    assert client.post("/verein/vorstand/treffen").status_code == 403
    store.logout(token)
    assert client.get("/verein/vorstand/treffen").status_code == 401
    with pytest.raises(events.verein.VereinDenied):
        events.save(token, data, identifier=identifier, version=1)


def test_board_editor_keeps_valid_input_on_failure_and_publishes_occurrence(membership):
    client, _store, _token, data, _events = _event(membership)
    path = "/verein/vorstand/treffen"
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    csrf = test_verein_membership._csrf(client, path)
    form = {key: value for key, value in data.items() if key != "translations"}
    for locale, values in data["translations"].items():
        form.update({key + "_" + locale: value for key, value in values.items()})
    form.update(csrf_token=csrf, status="scheduled", end="2027-03-28T09:00")
    invalid = client.post(path, data=form)
    assert invalid.status_code == 409
    assert 'role="alert"' in invalid.text
    assert "Fictional meetup en" in invalid.text
    form["end"] = "2027-03-28T11:00"
    assert client.post(path, data=form).status_code == 303
    assert client.get("/veranstaltungen/" + data["slug"]).status_code == 200


def test_published_occurrence_cannot_be_hidden_by_changing_back_to_draft(membership):
    _client, _store, token, data, events = _event(membership)
    data["status"] = "scheduled"
    identifier = events.save(token, data)
    data["status"] = "draft"
    with pytest.raises(events.verein.VereinDenied):
        events.save(token, data, identifier=identifier, version=1)
    assert events.published(data["slug"]) is not None


@pytest.mark.parametrize(
    "start,end",
    [
        ("2027-03-28T02:30", "2027-03-28T03:30"),
        ("2027-10-31T02:30", "2027-10-31T03:30"),
        ("2027-02-03T11:00", "2027-02-03T10:00"),
    ],
)
def test_invalid_or_ambiguous_local_times_never_publish(membership, start, end):
    _client, _store, token, data, events = _event(membership)
    data.update(start=start, end=end)
    with pytest.raises(events.verein.VereinDenied):
        events.save(token, data)
