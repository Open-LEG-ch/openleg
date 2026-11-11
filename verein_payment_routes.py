# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reviewed donation instructions; no payment initiation or settlement callback."""

import base64
from decimal import Decimal

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    session,
)
from flask_babel import get_locale

import database as db
import document_generator
import verein_payments
from i18n import gettext as _
from security_extensions import rate_limit
from site_release import require_complete_site
from verein_facts import facts
from verein_routes import csrf_token, require_csrf, require_reviewer

payments_bp = Blueprint("verein_payments", __name__)
payments_bp.before_request(require_complete_site)


@payments_bp.after_request
def no_cached_recipient(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@payments_bp.errorhandler(db.VereinUnavailable)
def unavailable(_error):
    return render_template(
        "verein/message.html",
        title=_("Dienst vorübergehend nicht verfügbar"),
        message=_("Bitte versuchen Sie es später erneut."),
    ), 503


@payments_bp.route("/verein/vorstand/zahlungsempfaenger", methods=["GET", "POST"])
def prepare():
    require_reviewer()
    errors = {}
    if request.method == "POST":
        require_csrf()
        try:
            identifier = db.prepare_verein_recipient(
                session["verein_session"], request.form
            )
            return redirect(
                "/verein/vorstand/zahlungsempfaenger/" + identifier, code=303
            )
        except verein_payments.PaymentInputError as exc:
            errors[exc.field] = str(exc)
    return render_template(
        "verein/recipient_edit.html",
        form=request.form,
        errors=errors,
        profiles=db.get_verein_recipient_review(session["verein_session"]),
        csrf_token=csrf_token(),
    ), 409 if errors else 200


@payments_bp.get("/verein/vorstand/zahlungsempfaenger/<identifier>")
def review(identifier):
    require_reviewer()
    profile = db.get_verein_recipient_review(session["verein_session"], identifier)
    if profile is None:
        abort(404)
    return render_template(
        "verein/recipient_review.html",
        profile=profile,
        error=None,
        csrf_token=csrf_token(),
        grouped_iban=verein_payments.grouped_iban,
    )


@payments_bp.post("/verein/vorstand/zahlungsempfaenger/<identifier>/freigeben")
def approve(identifier):
    require_reviewer()
    require_csrf()
    try:
        if any(
            request.form.get(name) != "yes"
            for name in ("ownership_checked", "address_checked", "qr_checked")
        ):
            raise db.VereinDenied("Actual checks required")
        db.approve_verein_recipient(
            session["verein_session"],
            identifier,
            request.form.get("evidence", ""),
            request.form.get("qr_evidence", ""),
        )
        return redirect("/verein/spenden", code=303)
    except (db.VereinDenied, verein_payments.PaymentInputError):
        profile = db.get_verein_recipient_review(session["verein_session"], identifier)
        if profile is None:
            abort(404)
        return render_template(
            "verein/recipient_review.html",
            profile=profile,
            error=_(
                "Keine Freigabe. Eine andere aktive berechtigte Person muss den neuesten Empfängerstand anhand der tatsächlichen Bank- und Adressbestätigung sowie der QR-Prüfung bestätigen."
            ),
            csrf_token=csrf_token(),
            grouped_iban=verein_payments.grouped_iban,
        ), 409


@payments_bp.post("/verein/vorstand/zahlungsempfaenger/<identifier>/zurueckziehen")
def withdraw(identifier):
    require_reviewer()
    require_csrf()
    try:
        db.withdraw_verein_recipient(
            session["verein_session"], identifier, request.form.get("reason", "")
        )
        return redirect("/verein/vorstand/zahlungsempfaenger/" + identifier, code=303)
    except (db.VereinDenied, verein_payments.PaymentInputError):
        profile = db.get_verein_recipient_review(session["verein_session"], identifier)
        if profile is None:
            abort(404)
        return render_template(
            "verein/recipient_review.html",
            profile=profile,
            error=_(
                "Die Zahlungsangaben wurden nicht ausgeblendet. Prüfen Sie den aktuellen Stand und geben Sie einen Grund an."
            ),
            csrf_token=csrf_token(),
            grouped_iban=verein_payments.grouped_iban,
        ), 409


def _active():
    if not facts(current_app.config).public_ready:
        return None
    return db.get_active_verein_recipient()


@payments_bp.get("/verein/spenden")
def donations():
    profile = _active()
    value = request.args.get("amount", "")
    error = None
    try:
        selected = verein_payments.amount(value)
    except verein_payments.PaymentInputError as exc:
        selected, error = "", str(exc)
    return render_template(
        "release/donations.html",
        page_title=_("Den Verein unterstützen"),
        page_description=_(
            "Freiwillige Beiträge für Software, Dokumentation und Austausch. Empfängerangaben erscheinen erst nach unabhängiger Prüfung."
        ),
        profile=profile,
        selected_amount=selected,
        entered_amount=value,
        error=error,
        grouped_iban=verein_payments.grouped_iban,
        site_url=current_app.config["SITE_URL"],
    ), 400 if error else 200


def _qr_profile(identifier):
    profile = _active()
    if profile is None:
        abort(404)
    if profile["id"] != identifier:
        abort(410)
    try:
        selected = verein_payments.amount(request.args.get("amount", ""))
    except verein_payments.PaymentInputError:
        abort(400)
    return profile, selected


@payments_bp.get("/verein/spenden/qr/<identifier>.svg")
def qr_svg(identifier):
    profile, selected = _qr_profile(identifier)
    return Response(
        verein_payments.svg(profile, selected),
        mimetype="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )


@payments_bp.get("/verein/spenden/qr/<identifier>.pdf")
@rate_limit("30 per hour")
def qr_pdf(identifier):
    profile, selected = _qr_profile(identifier)
    return _pdf(profile, selected)


@payments_bp.get("/verein/vorstand/zahlungsempfaenger/<identifier>/vorschau.<kind>")
def draft_qr(identifier, kind):
    require_reviewer()
    record = db.get_verein_recipient_review(session["verein_session"], identifier)
    if record is None or kind not in ("svg", "pdf"):
        abort(404)
    profile = record["public_snapshot"]
    if kind == "svg":
        return Response(verein_payments.svg(profile), mimetype="image/svg+xml")
    return _pdf(profile, "", draft=True)


def _pdf(profile, selected, *, draft=False):
    svg = verein_payments.svg(profile, selected)
    locale = str(get_locale())
    labels = QR_LABELS[locale]
    markup = document_generator.render_document_template(
        "verein/qr_bill.html",
        draft=draft,
        profile=profile,
        labels=labels,
        grouped_iban=verein_payments.grouped_iban(profile["iban"]),
        selected_amount=selected,
        amount_text=format(Decimal(selected), ",.2f").replace(",", " ")
        if selected
        else "",
        qr_uri="data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode(),
        compact=len(
            profile["name"] + profile["street"] + profile["city"] + profile["message"]
        )
        > 180,
    )
    return Response(
        document_generator.render_pdf_html(markup),
        mimetype="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="openleg-spende-qr.pdf"',
            "Cache-Control": "no-store",
        },
    )


# Prescribed functional labels, SIX IG 2.4 Annex C. No stylistic substitution.
QR_LABELS = {
    "de_CH": (
        "Empfangsschein",
        "Zahlteil",
        "Konto / Zahlbar an",
        "Zahlbar durch (Name/Adresse)",
        "Währung",
        "Betrag",
        "Annahmestelle",
        "Zusätzliche Informationen",
        "Vor der Einzahlung abzutrennen",
    ),
    "en": (
        "Receipt",
        "Payment part",
        "Account / Payable to",
        "Payable by (name/address)",
        "Currency",
        "Amount",
        "Acceptance point",
        "Additional information",
        "Separate before paying in",
    ),
    "fr_CH": (
        "Récépissé",
        "Section paiement",
        "Compte / Payable à",
        "Payable par (nom/adresse)",
        "Monnaie",
        "Montant",
        "Point de dépôt",
        "Informations supplémentaires",
        "A détacher avant le versement",
    ),
    "it_CH": (
        "Ricevuta",
        "Sezione pagamento",
        "Conto / Pagabile a",
        "Pagabile da (nome/indirizzo)",
        "Valuta",
        "Importo",
        "Punto di accettazione",
        "Informazioni supplementari",
        "Da staccare prima del versamento",
    ),
}
