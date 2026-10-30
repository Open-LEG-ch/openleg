# SPDX-License-Identifier: AGPL-3.0-or-later
"""Municipality notices use verified recipient choices, never caller language."""

from types import SimpleNamespace

import pytest
from flask import Flask

import email_automation
import interest_intake
from tests import test_interest_postgres

interest_database = test_interest_postgres.interest_database
pytestmark = pytest.mark.integration


def test_municipality_notice_uses_canonical_verified_consent_and_own_language(
    interest_database, monkeypatch
):
    db = interest_database
    for identifier, locale, consent in [
        ("french", "fr_CH", True),
        ("german", "de_CH", True),
        ("optout", "fr_CH", False),
    ]:
        assert db.save_building(
            identifier,
            identifier + "@example.invalid",
            {
                "address": "Synthetic address",
                "lat": 47.2,
                "lon": 8.2,
                "bfs_number": 2554,
            },
            {"updates_opt_in": consent},
            verified=True,
            preferred_locale=locale,
        )
    for identifier, email, locale, verify in [
        ("duplicate", "FRENCH@example.invalid", "de_CH", True),
        ("coverage", "coverage@example.invalid", "fr_CH", True),
        ("unverified", "german@example.invalid", "fr_CH", False),
        ("other-town", "other-town@example.invalid", "fr_CH", True),
    ]:
        assert db.save_coverage_request(
            request_id=identifier,
            email=email,
            address="Synthetic unresolved address",
            plz="8000",
            municipality_name="Synthetic municipality",
            canton="ZH",
            bfs_number=999 if identifier == "other-town" else 2554,
            roles=[],
            has_solar=False,
            verification_token="synthetic-" + identifier,
            preferred_locale=locale,
        )
        if verify:
            assert db.verify_coverage_request("synthetic-" + identifier)

    captured = {}
    monkeypatch.setattr(
        email_automation,
        "_send_email",
        lambda email, subject, body: (
            captured.setdefault(email, (subject, body)) is not None
        ),
    )
    app = Flask(__name__)
    app.config["BABEL_DEFAULT_LOCALE"] = "fr_CH"
    with app.test_request_context("/?lang=fr-CH"):
        result = email_automation.notify_new_municipality_interest(
            bfs_number=2554,
            municipality_name="Synthetic <script>municipality</script>",
            newcomer_email="newcomer@example.invalid",
            base_url="https://example.invalid",
        )
    assert result == {"sent": 3, "failed": 0}
    assert set(captured) == {
        "french@example.invalid",
        "german@example.invalid",
        "coverage@example.invalid",
    }
    for email in ["french@example.invalid", "coverage@example.invalid"]:
        subject, body = captured[email]
        assert "Nouvelle" in subject
        assert "lang=fr-CH" in body
        assert ">Se désinscrire</a>" in body
        assert ">Se déconnecter</a>" not in body
        assert "<script>" not in body
        assert "newcomer@example.invalid" not in body
    assert "Neue LEG-Interessenmeldung" in captured["german@example.invalid"][0]
    assert "lang=de-CH" in captured["german@example.invalid"][1]
    assert "4 bestätigte Interessierte" in captured["german@example.invalid"][1]
    assert db.get_verified_interest_recipients(2554) == sorted(captured)


def test_coverage_confirmation_uses_its_explicit_language_outside_flask(
    interest_database,
):
    captured = []
    result = interest_intake.submit(
        {
            "email": "synthetic@example.invalid",
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
    _email, subject, body = captured[0]
    assert "confirmer" in subject.lower()
    assert "lang=fr-CH" in body
    assert "Confirm" in body
