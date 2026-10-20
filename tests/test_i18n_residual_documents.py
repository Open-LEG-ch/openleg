# SPDX-License-Identifier: AGPL-3.0-or-later
"""Swiss French grammar and locale-only document presentation regressions."""

from copy import deepcopy
from pathlib import Path

import pytest
from flask import Flask, render_template
from flask_babel import force_locale
from weasyprint import HTML

import document_generator
import email_localization
import formation_wizard
import i18n
import member_invoices
from tests.test_billing_member_invoices import DETAIL_VIEW


@pytest.fixture
def localized_app():
    app = Flask(__name__, template_folder=str(Path(__file__).parents[1] / "templates"))
    app.config.update(
        BABEL_TRANSLATION_DIRECTORIES=str(Path(__file__).parents[1] / "translations"),
        BABEL_DEFAULT_LOCALE="de_CH",
    )
    i18n.init_app(app)
    app.jinja_env.globals["public_site_url"] = lambda path: path
    return app


@pytest.mark.parametrize("locale", ["de_CH", "fr_CH"])
@pytest.mark.parametrize("count", [0, 1, 2])
@pytest.mark.parametrize("day", [0, 7])
def test_scheduled_neighbor_sentence_uses_real_count(locale, count, day):
    body = email_localization.render_template(
        f"emails/day_{day}_{'welcome' if day == 0 else 'consumption'}.html",
        locale=locale,
        platform_name="Synthetic",
        city_name="Synthetic",
        utility_name="Synthetic",
        neighbor_count=count,
        primary_color="#123456",
        contact_email="test@example.invalid",
        dashboard_url="https://example.invalid/dashboard",
        unsubscribe_url="https://example.invalid/unsubscribe",
        site_url="https://example.invalid",
        referral_link="https://example.invalid/?ref=synthetic",
    )
    if count == 0:
        assert all(
            text not in body
            for text in [
                "0 ménages",
                "0 autres ménages",
                "0 Haushalte",
                "0 weitere Haushalte",
            ]
        )
    else:
        noun = (
            ("ménage" if count == 1 else "ménages")
            if locale == "fr_CH"
            else ("Haushalt" if count == 1 else "Haushalte")
        )
        if day == 0:
            phrase = (
                (f"{count} autre ménage" if count == 1 else f"{count} autres ménages")
                if locale == "fr_CH"
                else (
                    f"{count} weiterer Haushalt"
                    if count == 1
                    else f"{count} weitere Haushalte"
                )
            )
        else:
            phrase = f"{count} {noun}"
        assert phrase in body
        if count == 1:
            assert "1 ménages" not in body
            assert "1 autres" not in body
            assert "1 weitere Haushalte" not in body
            assert "1 Haushalte" not in body
        assert "lang=" + locale.replace("_", "-") in body


@pytest.mark.parametrize(
    "locale,formatted", [("de_CH", "4’500"), ("fr_CH", "4\u202f500")]
)
def test_contract_consumption_is_display_only(
    localized_app, monkeypatch, locale, formatted
):
    monkeypatch.setattr(document_generator, "_render_pdf", lambda html: html)
    quantity = 4500
    with localized_app.app_context(), force_locale(locale):
        rendered = document_generator.generate_teilnehmervertrag(
            "Synthetic",
            "Address",
            "Synthetic",
            "consumer",
            annual_consumption_kwh=quantity,
            date_str="2026-10-20",
        )
    assert formatted + " kWh" in rendered
    assert "4,500 kWh" not in rendered
    assert quantity == 4500


def invoice_fixture(days=1, rows=1):
    invoice = deepcopy(DETAIL_VIEW)
    invoice["policy_payment_days"] = days
    invoice["settlement_charges"] = [
        {**invoice["charges"][0], "item_type_label": "Abrechnungsentgelt"}
    ]
    invoice["battery_shares"] = [
        {**invoice["charges"][0], "item_type_label": "Quartierakku-Anteil"}
    ]
    invoice["charges"] = [
        {**invoice["charges"][0], "item_type_label": f"Synthetic position {index:03d}"}
        for index in range(rows)
    ]
    return invoice


@pytest.mark.parametrize("locale", ["de_CH", "fr_CH"])
@pytest.mark.parametrize("days", [1, 2])
def test_real_invoice_pdf_payment_grammar_numbers_and_short_pagination(
    localized_app, tmp_path, locale, days
):
    invoice = invoice_fixture(days)
    frozen = deepcopy(invoice)
    with localized_app.app_context(), force_locale(locale):
        html = member_invoices.render_pdf_html_string(invoice)
    expected = f"{days} " + (
        ("jour" if days == 1 else "jours")
        if locale == "fr_CH"
        else ("Tag" if days == 1 else "Tage")
    )
    assert expected in html
    assert "1 jours" not in html and "1 Tage" not in html
    document = HTML(string=html).render()
    pdf = document.write_pdf()
    (tmp_path / f"invoice-{locale}-{days}.pdf").write_bytes(pdf)
    assert pdf.startswith(b"%PDF-")
    assert len(document.pages) == 1
    text = " ".join(
        box.text
        for page in document.pages
        for box in page._page_box.descendants()
        if hasattr(box, "text")
    )
    assert expected in text
    for amount in [
        "12.08 CHF",
        "0.93 CHF",
        "13.01 CHF",
        "18.08",
        "-6.00",
        "2026-09-04",
    ]:
        assert amount in text
    assert "Synthetic position 000" in text
    assert "OpenLEG" in text
    assert invoice == frozen


@pytest.mark.parametrize("locale", ["de_CH", "fr_CH"])
def test_long_pdf_retains_every_position_and_is_allowed_multiple_pages(
    localized_app, locale
):
    invoice = invoice_fixture(rows=65)
    with localized_app.app_context(), force_locale(locale):
        document = HTML(string=member_invoices.render_pdf_html_string(invoice)).render()
    assert document.write_pdf().startswith(b"%PDF-")
    assert len(document.pages) > 1
    text = " ".join(
        box.text
        for page in document.pages
        for box in page._page_box.descendants()
        if hasattr(box, "text")
    )
    for index in range(65):
        assert f"Synthetic position {index:03d}" in text
    assert "13.01 CHF" in text


@pytest.mark.parametrize("municipality", [None, "Synthetic <b>commune</b>"])
def test_french_confirmation_uses_complete_escaped_sentence(
    localized_app, municipality
):
    with localized_app.test_request_context("/?lang=fr-CH"), force_locale("fr_CH"):
        rendered = render_template(
            "interest_confirmed.html", municipality_name=municipality
        )
    assert "Votre inscription" in rendered
    assert "est maintenant confirmée." in rendered
    assert "<b>commune</b>" not in rendered
    if municipality:
        assert "Synthetic &lt;b&gt;commune&lt;/b&gt;" in rendered


def test_formation_single_missing_neighbor_is_singular(localized_app):
    with localized_app.app_context(), force_locale("fr_CH"):
        count = formation_wizard.FORMATION_CONFIG["min_community_size"] - 1
        steps = formation_wizard._get_next_steps("interested", count)
    assert any("1 voisin supplémentaire" in step for step in steps)
    assert all("1 voisins" not in step for step in steps)


@pytest.mark.parametrize(
    "template,context,phrases,unwanted",
    [
        (
            "meter_upload.html",
            {"site_url": "http://localhost"},
            ["les voisins et la commune"],
            ["les voisins et la communauté"],
        ),
        (
            "admin/ops.html",
            {"counts": {}, "interest_registrations": [], "entries": [], "latest": {}},
            ["tableau de bord communal"],
            ["tableau de bord de la communauté"],
        ),
        (
            "leg_community_create.html",
            {},
            ["Modèle de répartition"],
            ["Modèle de distribution"],
        ),
        (
            "leg_gruenden.html",
            {"faq_page_jsonld": {}, "site_url": "http://localhost"},
            [
                "puissance de raccordement",
                "La CEL peut gérer",
                "représentant de la CEL",
            ],
            ["charge connectée", "représentant de CEL"],
        ),
        (
            "telemetry_dashboard.html",
            {"unavailable": False, "installations": [], "forecast": None},
            ["dans une CEL à laquelle votre adhésion est confirmée"],
            ["dans une LEG"],
        ),
    ],
)
def test_french_terminology_in_real_page_templates(
    localized_app, template, context, phrases, unwanted
):
    with localized_app.test_request_context("/?lang=fr-CH"), force_locale("fr_CH"):
        rendered = render_template(template, **context)
    for phrase in phrases:
        assert phrase in rendered
    for phrase in unwanted:
        assert phrase not in rendered


def test_real_out_of_range_fee_diagnostic_is_localized_without_mutating_policy(
    localized_app,
):
    from datetime import datetime
    from decimal import Decimal

    import billing_policy
    from tests.test_billing_policy import _IDENTITY_VALID_POLICY

    policy = deepcopy(_IDENTITY_VALID_POLICY)
    policy["settlement_fee_chf_per_kwh"] = Decimal(1001)
    frozen = deepcopy(policy)
    with pytest.raises(billing_policy.InvalidPersistedPolicy) as error:
        billing_policy.validate_persisted_policy(
            policy,
            period_start=datetime.fromisoformat("2026-09-01T00:00:00+02:00"),
            community_id="community-a",
        )
    diagnostic = {"settlement_fee_rp": str(error.value)}
    with localized_app.app_context(), force_locale("fr_CH"):
        messages = billing_policy.localize_form_errors(diagnostic)
    assert (
        messages["settlement_fee_rp"]
        == "Les frais de facturation définis dans la règle dépassent les limites autorisées."
    )
    assert "hors de portée" not in messages["settlement_fee_rp"]
    assert diagnostic["settlement_fee_rp"].startswith("Das Abrechnungsentgelt")
    assert policy == frozen
