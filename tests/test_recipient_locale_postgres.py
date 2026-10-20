# SPDX-License-Identifier: AGPL-3.0-or-later
"""Persist recipient choices without changing a verified owner's preference."""

from pathlib import Path
from types import SimpleNamespace

import psycopg2
import pytest
from flask import Flask

import email_automation
import interest_intake
from tests import test_dashboard_access_routes, test_interest_postgres

_set_session = test_dashboard_access_routes._set_session
app_module = test_dashboard_access_routes.app_module
interest_database = test_interest_postgres.interest_database

pytestmark = pytest.mark.integration


def save_profile(db, **options):
    return db.save_building(
        "SYNTHETIC-LOCALE-OWNER",
        "locale@example.invalid",
        {"address": "Synthetic address", "lat": 47.2, "lon": 8.2},
        {},
        **options,
    )


def test_existing_registration_path_defaults_to_german(interest_database):
    db = interest_database
    assert save_profile(db)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT preferred_locale FROM buildings")
        assert cur.fetchone()["preferred_locale"] == "de_CH"


@pytest.mark.parametrize("selected,expected", [("fr-CH", "fr_CH"), ("de-CH", "de_CH")])
def test_new_registration_persists_an_explicit_supported_choice(
    interest_database, selected, expected
):
    db = interest_database
    assert save_profile(db, preferred_locale=selected)
    db.create_tables()
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT preferred_locale FROM buildings")
        assert cur.fetchone()["preferred_locale"] == expected


def test_reregistration_cannot_change_a_verified_profiles_language(interest_database):
    db = interest_database
    assert save_profile(db, preferred_locale="fr_CH", verified=True)

    assert save_profile(db, preferred_locale="de_CH")

    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT preferred_locale, verified FROM buildings")
        row = cur.fetchone()
    assert row["preferred_locale"] == "fr_CH"
    assert row["verified"] is True


@pytest.mark.parametrize("selected", ["it_CH", "../../fr_CH", None])
def test_unsupported_profile_choice_is_refused_before_write(
    interest_database, selected
):
    with pytest.raises(ValueError, match="unsupported_recipient_locale"):
        save_profile(interest_database, preferred_locale=selected)
    with interest_database.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS count FROM buildings")
        assert cur.fetchone()["count"] == 0


def test_address_coverage_request_keeps_its_own_choice(interest_database):
    db = interest_database
    assert db.save_coverage_request(
        request_id="SYNTHETIC-LOCALE-COVERAGE",
        email="coverage@example.invalid",
        address="Synthetic unresolved address",
        plz="8000",
        municipality_name="Synthetic municipality",
        canton="ZH",
        bfs_number=261,
        roles=["consumer"],
        has_solar=False,
        verification_token="synthetic-locale-confirmation",
        preferred_locale="fr-CH",
    )
    db.create_tables()
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT preferred_locale FROM coverage_requests")
        assert cur.fetchone()["preferred_locale"] == "fr_CH"


def test_database_refuses_unsupported_persisted_locale(interest_database):
    db = interest_database
    assert save_profile(db)
    with pytest.raises(psycopg2.IntegrityError):
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE buildings SET preferred_locale = 'it_CH'")


@pytest.mark.parametrize("first_locale", ["fr_CH", "de_CH"])
def test_real_queue_delivers_each_stored_language_under_opposite_default(
    interest_database, monkeypatch, first_locale
):
    db = interest_database
    monkeypatch.setattr(db, "_db_initialized", True)
    locales = [first_locale, "de_CH" if first_locale == "fr_CH" else "fr_CH"]
    for number, locale in enumerate(locales):
        identifier = f"SYNTHETIC-MAIL-{number}"
        email = f"queue-{locale}@example.invalid"
        assert db.save_building(
            identifier,
            email,
            {"address": "<script>synthetic address</script>", "lat": 47.2, "lon": 8.2},
            {"updates_opt_in": True},
            verified=True,
            preferred_locale=locale,
        )
        assert db.schedule_email(identifier, email, "day_0_welcome", number + 1)

    delivered = {}
    monkeypatch.setattr(
        email_automation,
        "_send_email",
        lambda email, subject, body: (
            delivered.setdefault(email, (subject, body)) is not None
        ),
    )
    app = Flask(__name__, template_folder=str(Path(__file__).parents[1] / "templates"))
    app.config.update(
        APP_BASE_URL="https://example.invalid", BABEL_DEFAULT_LOCALE="fr_CH"
    )
    with app.test_request_context("/?lang=de-CH"):
        result = email_automation.process_email_queue(app)
    assert result == {"sent": 2, "failed": 0, "total": 2}
    french = delivered["queue-fr_CH@example.invalid"]
    german = delivered["queue-de_CH@example.invalid"]
    assert "Bienvenue" in french[0]
    assert "Ouvrir mon tableau de bord" in french[1]
    assert "Willkommen" in german[0]
    assert "Mein Dashboard öffnen" in german[1]
    assert 'lang="fr-CH"' in french[1]
    assert 'lang="de-CH"' in german[1]
    assert "lang=fr-CH" in french[1]
    assert "lang=de-CH" in german[1]
    assert "<script>" not in french[1] + german[1]
    assert email_automation.process_email_queue(app)["total"] == 0
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT email, status FROM scheduled_emails")
        assert all(
            row["email"] == "" and row["status"] == "sent" for row in cur.fetchall()
        )


def test_owner_language_update_requires_session_and_csrf_and_ignores_selector(
    interest_database, app_module, monkeypatch
):
    db = interest_database
    monkeypatch.setattr(db, "_db_initialized", True)
    assert save_profile(db, verified=True)
    assert db.save_building(
        "SYNTHETIC-OTHER-OWNER",
        "other@example.invalid",
        {"address": "Synthetic second address", "lat": 47.2, "lon": 8.2},
        {},
        verified=True,
    )
    client = app_module.web.test_client()
    payload = {
        "preferred_locale": "fr-CH",
        "csrf_token": "csrf-secret",
        "building_id": "SYNTHETIC-OTHER-OWNER",
    }
    assert client.post("/dashboard/language", data=payload).status_code == 401
    _set_session(client, building_id="SYNTHETIC-LOCALE-OWNER")
    assert (
        client.post(
            "/dashboard/language", data={**payload, "csrf_token": "wrong"}
        ).status_code
        == 400
    )
    assert db.get_building("SYNTHETIC-LOCALE-OWNER")["preferred_locale"] == "de_CH"
    response = client.post("/dashboard/language", data=payload)
    assert response.status_code == 302
    assert "lang=fr-CH" in response.headers["Location"]
    assert db.get_building("SYNTHETIC-LOCALE-OWNER")["preferred_locale"] == "fr_CH"
    assert db.get_building("SYNTHETIC-OTHER-OWNER")["preferred_locale"] == "de_CH"
    assert (
        client.post(
            "/dashboard/language", data={**payload, "preferred_locale": "it_CH"}
        ).status_code
        == 400
    )
    assert db.get_building("SYNTHETIC-LOCALE-OWNER")["preferred_locale"] == "fr_CH"


def test_coverage_intake_persists_choice_without_delivering_real_mail(
    interest_database,
):
    captured = []
    result = interest_intake.submit(
        {
            "email": "coverage@example.invalid",
            "plz": "8000",
            "municipality_name": "Synthetic municipality",
            "preferred_locale": "fr-CH",
        },
        db=interest_database,
        security=SimpleNamespace(
            validate_email_address=lambda email: (True, email, None),
            sanitize_string=lambda value, **kwargs: value,
        ),
        base_url="https://example.invalid",
        send_email=lambda *args: captured.append(args),
    )
    assert result["accepted"] is True
    assert len(captured) == 1
    with interest_database.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT preferred_locale, verified FROM coverage_requests")
        row = cur.fetchone()
    assert row["preferred_locale"] == "fr_CH"
    assert row["verified"] is False


def test_legacy_profile_migration_backfills_german_and_keeps_constraint(
    interest_database,
):
    db = interest_database
    assert save_profile(db)
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("DROP VIEW verified_interest")
        cur.execute("ALTER TABLE buildings DROP COLUMN preferred_locale")
        cur.execute("ALTER TABLE coverage_requests DROP COLUMN preferred_locale")
    db.create_tables()
    db.create_tables()
    assert db.get_building("SYNTHETIC-LOCALE-OWNER")["preferred_locale"] == "de_CH"
    with pytest.raises(psycopg2.IntegrityError):
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE buildings SET preferred_locale = 'fr_FR'")


@pytest.mark.parametrize("failure,status", [("unverified", 403), ("database", 503)])
def test_owner_language_update_fails_without_changing_saved_choice(
    interest_database, app_module, monkeypatch, failure, status
):
    db = interest_database
    monkeypatch.setattr(db, "_db_initialized", True)
    assert save_profile(db, verified=failure != "unverified")
    if failure == "database":
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute("""CREATE FUNCTION reject_language() RETURNS trigger AS $$
                BEGIN RAISE EXCEPTION 'synthetic private database detail'; END;
                $$ LANGUAGE plpgsql""")
            cur.execute("""CREATE TRIGGER reject_language BEFORE UPDATE ON buildings
                FOR EACH ROW EXECUTE FUNCTION reject_language()""")
    client = app_module.web.test_client()
    _set_session(client, building_id="SYNTHETIC-LOCALE-OWNER")
    response = client.post(
        "/dashboard/language",
        data={"preferred_locale": "fr_CH", "csrf_token": "csrf-secret"},
    )
    assert response.status_code == status
    assert "synthetic private database detail" not in response.get_data(as_text=True)
    assert db.get_building("SYNTHETIC-LOCALE-OWNER")["preferred_locale"] == "de_CH"
