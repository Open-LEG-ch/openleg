# SPDX-License-Identifier: AGPL-3.0-or-later
"""The dormant reminder renders safely in either recipient language."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from flask import has_app_context, has_request_context

from email_localization import render_template

CONTEXT = {
    "primary_color": "#123456",
    "platform_name": "OpenLEG",
    "community_name": "Synthetic LEG",
    "current_status": "Aktiv",
    "next_step_description": "Erstellen Sie die rechtlichen Dokumente.",
    "contact_email": "support@example.ch",
    "dashboard_url": "https://example.ch/leg/dashboard?cid=synthetic",
    "unsubscribe_url": "https://example.ch/unsubscribe?token=synthetic",
}


@pytest.mark.parametrize(
    "locale,days,phrase",
    [
        ("de_CH", 0, "seit 0 Tagen"),
        ("de_CH", 1, "seit 1 Tag"),
        ("de_CH", 2, "seit 2 Tagen"),
        ("fr_CH", 0, "depuis 0 jour"),
        ("fr_CH", 1, "depuis 1 jour"),
        ("fr_CH", 2, "depuis 2 jours"),
    ],
)
def test_reminder_worker_renders_recipient_language_and_plurals(locale, days, phrase):
    assert not has_app_context() and not has_request_context()
    html = render_template(
        "emails/formation_nudge.html", locale=locale, days_stuck=days, **CONTEXT
    )
    assert 'lang="' + locale.replace("_", "-") + '"' in html
    assert phrase in html
    assert (
        "Votre création de CEL est en attente"
        if locale == "fr_CH"
        else "Ihre LEG-Gründung wartet"
    ) in html
    assert "lang=" + locale.replace("_", "-") in html
    if locale == "fr_CH":
        assert "Préparez les documents juridiques." in html
        assert "Erstellen Sie die rechtlichen Dokumente." not in html


def test_reminder_escapes_community_and_operator_text():
    fields = {
        **CONTEXT,
        "community_name": "<script>private()</script>",
        "current_status": "<img src=x onerror=bad()>",
        "next_step_description": "<a href=evil>click</a>",
    }
    html = render_template(
        "emails/formation_nudge.html", locale="fr_CH", days_stuck=1, **fields
    )
    assert "<script>" not in html and "<img" not in html and "<a href=evil>" not in html
    assert (
        "&lt;script&gt;" in html and "&lt;img" in html and "&lt;a href=evil&gt;" in html
    )


def test_parallel_reminders_keep_recipient_language_isolated():
    locales = ["de_CH", "fr_CH"] * 10

    def render(locale):
        return render_template(
            "emails/formation_nudge.html", locale=locale, days_stuck=2, **CONTEXT
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        rendered = list(pool.map(render, locales))
    for locale, html in zip(locales, rendered, strict=True):
        assert ("Votre création de CEL est en attente" in html) == (locale == "fr_CH")
        assert 'lang="' + locale.replace("_", "-") + '"' in html
