# SPDX-License-Identifier: AGPL-3.0-or-later
"""Translated printable documents preserve frozen amounts and escaped fields."""

import re
from copy import deepcopy
from pathlib import Path

import pytest
from flask import Flask
from flask_babel import force_locale

import document_generator
import member_invoices
from i18n import format_period_label, init_app
from tests.test_billing_member_invoices import DETAIL_VIEW


@pytest.fixture
def web():
    web = Flask(__name__)
    web.config["BABEL_TRANSLATION_DIRECTORIES"] = str(
        Path(__file__).resolve().parents[1] / "translations"
    )
    init_app(web)
    return web


def test_french_invoice_uses_frozen_figures_and_localized_period_without_mutation(web):
    invoice = deepcopy(DETAIL_VIEW)
    original = deepcopy(invoice)
    with web.app_context(), force_locale("fr_CH"):
        french = member_invoices.render_pdf_html_string(invoice)
    with web.app_context(), force_locale("de_CH"):
        german = member_invoices.render_pdf_html_string(invoice)
    assert "Facture LEG-2026-000001" in french
    assert "juillet 2026" in french
    assert "Rechnung LEG-2026-000001" in german
    assert "Juli 2026" in german
    for figure in ("120.500", "15.00", "18.08", "-6.00", "13.01", "12.08", "0.93"):
        assert figure in french and figure in german
    assert "TVA" in french
    assert "Rp./kWh" not in french and "ct./kWh" in french
    assert "Produzentengutschrift" not in french
    assert invoice == original


def test_french_invoice_still_escapes_untrusted_snapshot_values(web):
    invoice = deepcopy(DETAIL_VIEW)
    invoice["issuer_name"] = '<img src=x onerror="alert(1)">'
    with web.app_context(), force_locale("fr_CH"):
        french = member_invoices.render_pdf_html_string(invoice)
    assert "<img" not in french
    assert "&lt;img" in french


def test_french_invoice_explains_an_empty_consumption_table(web):
    invoice = deepcopy(DETAIL_VIEW)
    invoice["charges"] = []
    with web.app_context(), force_locale("fr_CH"):
        french = member_invoices.render_pdf_html_string(invoice)
    assert "Aucun coût de consommation" in french


def test_formation_pdf_translates_text_and_keeps_names_and_escaping(web, monkeypatch):
    monkeypatch.setattr(document_generator, "_render_pdf", lambda html: html)
    with web.app_context(), force_locale("fr_CH"):
        french = document_generator.generate_gemeinschaftsvereinbarung(
            "BIVIS <script>",
            [
                {"name": "Producteur <b>", "address": "Adresse 1", "role": "producer"},
                {"name": "Membre", "address": "Adresse 2", "role": "consumer"},
            ],
            "Vully-les-Lacs",
            "proportional",
            date_str="2026-10-19",
        )
    assert "Convention de communauté" in french
    assert "Communauté électrique locale :" in french
    assert "Vully-les-Lacs" in french
    assert "2026-10-19" in french
    assert "Producteur" in french and "Consommateur" in french
    assert "<script>" not in french and "&lt;script&gt;" in french
    assert "Producteur &lt;b&gt;" in french
    assert "LApEl" in french and "OApEl" in french


def test_period_label_only_localizes_recognized_month_labels(web):
    with web.app_context(), force_locale("fr_CH"):
        assert format_period_label("September 2026") == "septembre 2026"
        assert format_period_label("Vully-les-Lacs") == "Vully-les-Lacs"
        assert format_period_label("Untrusted <script>") == "Untrusted <script>"
    assert format_period_label("September 2026") == "September 2026"


@pytest.mark.parametrize("kind", ["participant", "dso"])
def test_other_document_forms_isolate_locale_escape_inputs_and_keep_default(
    web, monkeypatch, kind
):
    monkeypatch.setattr(document_generator, "_render_pdf", lambda html: html)
    if kind == "participant":
        generate = document_generator.generate_teilnehmervertrag
        arguments = {
            "participant_name": "SYNTHETIC <script>",
            "participant_address": "Synthetic address",
            "role": "producer",
            "community_name": "Synthetic CEL",
            "pv_kwp": 5,
            "annual_consumption_kwh": 4500,
            "date_str": "2026-10-19",
        }
    else:
        generate = document_generator.generate_dso_anmeldung
        arguments = {
            "community_name": "SYNTHETIC <script>",
            "dso_name": "Synthetic VNB",
            "participants": [
                {
                    "name": "Synthetic member",
                    "address": "Synthetic address",
                    "metering_point": "SYNTHETIC-POINT",
                }
            ],
            "total_pv_kwp": 5,
            "network_level": "NE7",
            "date_str": "2026-10-19",
        }
    original = deepcopy(arguments)
    rendered = []
    for language in ("fr_CH", "de_CH", "fr_CH"):
        with web.app_context(), force_locale(language):
            rendered.append(generate(**arguments))
    french, german, french_again = rendered
    assert french == french_again
    assert '<html lang="fr-CH">' in french
    assert '<html lang="de-CH">' in german
    assert re.search(r"<h1>(.*?)</h1>", french).group(1) != re.search(
        r"<h1>(.*?)</h1>", german
    ).group(1)
    assert "<script>" not in french and "&lt;script&gt;" in french
    assert arguments == original
    assert generate(**arguments) == german
