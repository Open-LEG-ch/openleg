# SPDX-License-Identifier: AGPL-3.0-or-later
"""PDF document generator for LEG formation documents using WeasyPrint."""

import html
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import current_app, has_app_context
from flask_babel import get_locale
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from i18n import format_number_label, ngettext
from i18n import gettext as _
from translation_markers import gettext_noop

DISTRIBUTION_LABELS = {
    "einfach": gettext_noop("Gleichmässige Verteilung"),
    "proportional": gettext_noop("Proportionale Verteilung nach Verbrauchsanteil"),
    "individuell": gettext_noop("Individuelle Vereinbarung gemäss Anhang"),
}


_document_templates = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
    extensions=["jinja2.ext.i18n"],
    keep_trailing_newline=True,
)
_document_templates.install_gettext_callables(_, ngettext, newstyle=False)


def render_document_template(name, **values):
    """Render trusted document markup with already-escaped original values."""
    return _document_templates.get_template(name).render(
        current_locale=(
            str(get_locale()).replace("_", "-")
            if has_app_context() and "babel" in current_app.extensions
            else "de-CH"
        ),
        **values,
    )


def _escape_html(value):
    return html.escape(str(value), quote=True)


def _today_iso():
    return datetime.now(ZoneInfo("Europe/Zurich")).date().isoformat()


def _render_pdf(html_str):
    """Render HTML string to PDF bytes."""
    from weasyprint import HTML

    return HTML(string=html_str).write_pdf()


def render_pdf_html(html_str):
    """Render trusted, already-escaped document HTML through the PDF backend."""
    return _render_pdf(html_str)


def generate_gemeinschaftsvereinbarung(
    community_name,
    participants,
    municipality,
    distribution_model,
    date_str=None,
):
    """Generate LEG community agreement (Gemeinschaftsvereinbarung) as PDF.

    Args:
        community_name: Name of the LEG community
        participants: List of dicts with keys: name, address, role (producer/consumer)
        municipality: Municipality name
        distribution_model: einfach, proportional, or individuell
        date_str: Optional date string (YYYY-MM-DD), defaults to today

    Returns:
        PDF bytes

    Raises:
        ValueError: If fewer than 2 participants or no producer
    """
    if len(participants) < 2:
        raise ValueError("Eine LEG benötigt mindestens 2 Teilnehmer")

    has_producer = any(p.get("role") == "producer" for p in participants)
    if not has_producer:
        raise ValueError("Eine LEG benötigt mindestens einen Produzent")

    if date_str is None:
        date_str = _today_iso()

    community_name = _escape_html(community_name)
    municipality = _escape_html(municipality)
    date_str = _escape_html(date_str)
    dist_label = _escape_html(
        _(DISTRIBUTION_LABELS.get(distribution_model, distribution_model))
    )

    rows = ""
    for i, p in enumerate(participants, 1):
        role_label = _("Produzent") if p["role"] == "producer" else _("Konsument")
        rows += (
            f"<tr><td>{i}</td><td>{_escape_html(p['name'])}</td>"
            f"<td>{_escape_html(p['address'])}</td><td>{role_label}</td></tr>"
        )

    html = render_document_template(
        "documents/community_agreement.html",
        community_name=Markup(community_name),
        municipality=Markup(municipality),
        date_str=Markup(date_str),
        participant_rows=Markup(rows),
        distribution_label=Markup(dist_label),
        signature_rows=Markup(
            "".join(
                "<tr><td>" + _escape_html(p["name"]) + "</td><td></td><td></td></tr>"
                for p in participants
            )
        ),
    )

    return _render_pdf(html)


def generate_teilnehmervertrag(
    participant_name,
    participant_address,
    community_name,
    role,
    pv_kwp=0,
    annual_consumption_kwh=0,
    date_str=None,
):
    """Generate individual participant contract as PDF.

    Returns:
        PDF bytes
    """
    if date_str is None:
        date_str = _today_iso()

    participant_name = _escape_html(participant_name)
    participant_address = _escape_html(participant_address)
    community_name = _escape_html(community_name)
    date_str = _escape_html(date_str)
    role_label = _("Produzent") if role == "producer" else _("Konsument")

    pv_section = ""
    if pv_kwp and pv_kwp > 0:
        pv_section = (
            f"<p><strong>{_('PV-Anlage:')}</strong> {_escape_html(pv_kwp)} kWp</p>"
        )

    html = render_document_template(
        "documents/participant_contract.html",
        community_name=Markup(community_name),
        date_str=Markup(date_str),
        participant_name=Markup(participant_name),
        participant_address=Markup(participant_address),
        role_label=Markup(role_label),
        annual_consumption=Markup(format_number_label(annual_consumption_kwh)),
        pv_section=Markup(pv_section),
        signature_name=Markup(participant_name),
        signature_community=Markup(community_name),
    )

    return _render_pdf(html)


def generate_dso_anmeldung(
    community_name,
    dso_name,
    participants,
    total_pv_kwp,
    network_level,
    date_str=None,
):
    """Generate DSO registration form as PDF.

    Args:
        participants: List of dicts with keys: name, address, metering_point

    Raises:
        ValueError: If any participant missing metering_point
    """
    for p in participants:
        if not p.get("metering_point"):
            raise ValueError(f"Messpunkt fehlt für Teilnehmer {p.get('name', '?')}")

    if date_str is None:
        date_str = _today_iso()

    community_name = _escape_html(community_name)
    dso_name = _escape_html(dso_name)
    network_level = _escape_html(network_level)
    date_str = _escape_html(date_str)
    rows = ""
    for i, p in enumerate(participants, 1):
        rows += (
            f"<tr><td>{i}</td><td>{_escape_html(p['name'])}</td>"
            f"<td>{_escape_html(p['address'])}</td>"
            f"<td>{_escape_html(p['metering_point'])}</td></tr>"
        )

    html = render_document_template(
        "documents/dso_registration.html",
        dso_name=Markup(dso_name),
        date_str=Markup(date_str),
        community_name=Markup(community_name),
        network_level=Markup(network_level),
        total_pv_kwp=Markup(_escape_html(total_pv_kwp)),
        participant_rows=Markup(rows),
    )

    return _render_pdf(html)


def store_document(community_id, doc_type, pdf_bytes, filename):
    """Store generated document in database.

    Returns:
        Document ID
    """
    return db_store_document(community_id, doc_type, pdf_bytes, filename)


def db_store_document(community_id, doc_type, pdf_bytes, filename):
    """Store document in database. Placeholder for database integration."""
    import database

    return database.store_leg_document(community_id, doc_type, pdf_bytes, filename)


def list_documents(community_id):
    """List all documents for a community."""
    import database

    return database.list_leg_documents(community_id)
