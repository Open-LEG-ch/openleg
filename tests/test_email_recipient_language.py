# SPDX-License-Identifier: AGPL-3.0-or-later
"""Workers render independent, escaped recipient messages without Flask."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from email_automation import get_email_sequence
from email_localization import render_template


def render(number, locale, template):
    marker = f"SYNTHETIC-RECIPIENT-{number:03d}"
    body = render_template(
        template,
        locale=locale,
        platform_name=marker + "<script>private</script>",
        city_name="Synthetic <b>city</b>",
        utility_name="Synthetic <b>utility</b>",
        neighbor_count=3,
        primary_color="#123456",
        contact_email="contact@example.invalid",
        dashboard_url="https://example.invalid/dashboard/access/synthetic-link",
        unsubscribe_url="https://example.invalid/unsubscribe",
        site_url="https://example.invalid",
        referral_link="https://example.invalid/?ref=synthetic",
    )
    return marker, body


@pytest.mark.parametrize("locale", ["fr_CH", "de_CH"])
@pytest.mark.parametrize(
    "template,french,german",
    [
        (
            "emails/day_0_welcome.html",
            "Ouvrir mon tableau de bord",
            "Mein Dashboard öffnen",
        ),
        (
            "emails/day_3_smartmeter.html",
            "Avez-vous un compteur intelligent",
            "Haben Sie einen Smart Meter",
        ),
        (
            "emails/day_7_consumption.html",
            "Compléter les données de consommation",
            "Verbrauchsdaten ergänzen",
        ),
        ("emails/day_14_formation.html", "Commencer la création", "Gründung starten"),
    ],
)
def test_every_scheduled_template_renders_outside_flask(
    locale, template, french, german
):
    _marker, body = render(1, locale, template)
    phrase = french if locale == "fr_CH" else german
    assert phrase in body
    if locale == "fr_CH":
        assert ">Se désinscrire</a>" in body
        assert ">Se déconnecter</a>" not in body
    assert f'lang="{locale.replace("_", "-")}"' in body
    assert "<script>" not in body
    assert "&lt;script&gt;private&lt;/script&gt;" in body
    assert "<b>city</b>" not in body
    assert "<b>utility</b>" not in body
    assert "lang=" + locale.replace("_", "-") in body


def test_concurrent_recipient_messages_do_not_share_language_or_user_values():
    def one(number):
        locale = "fr_CH" if number % 2 else "de_CH"
        marker, body = render(number, locale, "emails/day_0_welcome.html")
        assert ("Bienvenue" if number % 2 else "Willkommen") in body
        assert marker in body
        assert all(
            f"SYNTHETIC-RECIPIENT-{other:03d}" not in body
            for other in range(100)
            if other != number
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(one, range(100)))


def test_mail_renderer_refuses_unlisted_templates():
    with pytest.raises(ValueError, match="unsupported_email_template"):
        render_template("../../secret", locale="fr_CH")


def test_sequence_subjects_use_explicit_language_without_flask():
    french = get_email_sequence("Synthetic platform", locale="fr_CH")
    german = get_email_sequence("Synthetic platform", locale="de_CH")
    assert "Bienvenue" in french["day_0_welcome"]["subject"]
    assert "compteur intelligent" in french["day_3_smartmeter"]["subject"]
    assert "Améliorez" in french["day_7_consumption"]["subject"]
    assert "communauté" in french["day_14_formation"]["subject"]
    assert "Willkommen" in german["day_0_welcome"]["subject"]
