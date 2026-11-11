# SPDX-License-Identifier: AGPL-3.0-or-later
"""Association facts through readiness callers, policy and localized pages."""

from datetime import UTC, datetime

import pytest

from tests import test_i18n

app_module = test_i18n.app_module
isolated_environment = test_i18n.isolated_environment
web = test_i18n.web


@pytest.mark.parametrize(
    "key,value,ready",
    [
        ("VEREIN_FORMATION_DATE", "", False),
        ("VEREIN_FORMATION_DATE", "invalid", False),
        ("VEREIN_FORMATION_DATE", "2999-01-01", False),
        ("VEREIN_FORMATION_DATE", "2026-11-01", True),
        ("VEREIN_CONTACT_ADDRESS", "", False),
        ("VEREIN_CONTACT_ADDRESS", "   ", False),
        ("VEREIN_PRIVACY_VERSION", "", False),
        ("VEREIN_DATA_CONTROLLER", "", False),
        ("VEREIN_STATUTES_URL", "", True),
        ("VEREIN_ACCEPTING_APPLICATIONS", False, True),
    ],
)
def test_shared_readiness_preserves_independent_permissions(
    web, monkeypatch, key, value, ready
):
    import database
    from site_release import accepting_applications
    from verein_payment_routes import _active

    web.config.update(
        VEREIN_FORMATION_DATE="2026-11-01",
        VEREIN_CONTACT_ADDRESS="Test address",
        VEREIN_PRIVACY_VERSION="test-v1",
        VEREIN_DATA_CONTROLLER="Test controller",
        VEREIN_STATUTES_URL="/test.pdf",
        VEREIN_ACCEPTING_APPLICATIONS=True,
    )
    web.config[key] = value
    recipient = {"id": "independently approved test recipient"}
    monkeypatch.setattr(database, "get_active_verein_recipient", lambda: recipient)
    with web.app_context():
        assert (_active() is recipient) is ready
        assert accepting_applications() is (
            ready
            and bool(web.config["VEREIN_STATUTES_URL"])
            and web.config["VEREIN_ACCEPTING_APPLICATIONS"]
        )
        monkeypatch.setattr(database, "get_active_verein_recipient", lambda: None)
        assert _active() is None


def test_formation_uses_zurich_midnight():
    from verein_facts import facts

    config = {"VEREIN_FORMATION_DATE": "2026-11-01"}
    assert (
        facts(config, now=datetime(2026, 10, 31, 22, 59, tzinfo=UTC)).formation_date
        is None
    )
    assert (
        str(facts(config, now=datetime(2026, 10, 31, 23, 0, tzinfo=UTC)).formation_date)
        == "2026-11-01"
    )


@pytest.mark.parametrize(
    "locale,founding",
    [
        ("de", "1. November 2026"),
        ("en", "November 1, 2026"),
        ("fr", "1 novembre 2026"),
        ("it", "1 novembre 2026"),
    ],
)
def test_localized_facts_follow_policy_without_rewriting_sentences(
    web, monkeypatch, locale, founding
):
    from dataclasses import replace

    from flask import render_template

    import verein_facts
    import verein_policy

    web.config.update(SITE_RELEASE_ENABLED=True, VEREIN_FORMATION_DATE="2026-11-01")
    client = web.test_client()
    original = client.get(f"/verein?lang={locale}").text
    assert founding in original
    assert "Obersiggenthal AG" in original
    assert "Verein openLEG Schweiz" in original
    monkeypatch.setattr(
        verein_facts,
        "POLICY",
        replace(
            verein_facts.POLICY,
            legal_name="Test association",
            seat="Test seat",
            annual_dues_chf=73,
            payment_days=31,
            reminder_days=61,
            contact_email="facts@example.invalid",
        ),
    )
    page = client.get(f"/verein?lang={locale}").text
    for value in (
        "Test association",
        "Test seat",
        "CHF 73",
        "31",
        "61",
        "facts@example.invalid",
    ):
        assert value in page
    with web.test_request_context(f"/verein/beitreten?lang={locale}"):
        web.preprocess_request()
        join = render_template("verein/join.html")
        assert "CHF 73" in join and "31" in join
    accepted = datetime(2026, 11, 1, tzinfo=UTC)
    assert str(verein_policy.admission_dates(accepted)[1]) == "2026-12-02"
    assert not verein_policy.can_terminate(
        now=datetime(2026, 12, 31, tzinfo=UTC), reminder_sent_at=accepted, paid_at=None
    )


@pytest.mark.parametrize("kind", ["accepted", "renewal"])
@pytest.mark.parametrize("locale", ["de_CH", "en", "fr_CH", "it_CH"])
def test_notices_use_recorded_amount_and_deadline(web, monkeypatch, kind, locale):
    from datetime import date
    from decimal import Decimal

    import email_utils
    from verein_routes import _send_notice

    messages = []
    monkeypatch.setattr(
        email_utils,
        "send_email",
        lambda email, subject, body, **kwargs: messages.append(body),
    )
    with web.app_context():
        _send_notice(
            {
                "kind": kind,
                "preferred_locale": locale,
                "email": "test@example.invalid",
                "amount_chf": Decimal("37.25"),
                "due_on": date(2024, 3, 30),
                "period_start": date(2024, 2, 29),
            }
        )
    assert ("37,25" if locale == "fr_CH" else "37.25") in messages[0]
    assert "2024" in messages[0]
    assert "CHF 50" not in messages[0]


def test_policy_keeps_thirty_day_dues_sixty_day_reminders_and_leap_anniversary():
    from datetime import date, timedelta

    from verein_policy import admission_dates, can_terminate, renewal_periods

    accepted = datetime(2024, 2, 29, 12, tzinfo=UTC)
    assert admission_dates(accepted) == (date(2024, 2, 29), date(2024, 3, 30))
    periods = renewal_periods(accepted, datetime(2028, 2, 29, 12, tzinfo=UTC))
    assert periods == [
        (date(2025, 2, 28), date(2025, 3, 30)),
        (date(2026, 2, 28), date(2026, 3, 30)),
        (date(2027, 2, 28), date(2027, 3, 30)),
        (date(2028, 2, 29), date(2028, 3, 30)),
    ]
    assert not can_terminate(
        now=accepted + timedelta(days=60, seconds=-1),
        reminder_sent_at=accepted,
        paid_at=None,
    )
    assert can_terminate(
        now=accepted + timedelta(days=60), reminder_sent_at=accepted, paid_at=None
    )


@pytest.mark.parametrize("configured", ["", None, "invalid", "2999-01-01"])
def test_unusable_formation_evidence_is_retained_without_claiming_founded(configured):
    from verein_facts import facts

    association = facts({"VEREIN_FORMATION_DATE": configured})
    assert association.configured_formation_date == configured
    assert association.formation_date is None
    assert not association.public_ready
