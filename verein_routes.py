# SPDX-License-Identifier: AGPL-3.0-or-later
"""Association intake HTTP boundary, distinct from LEG and dashboard membership."""

import hmac
import secrets
from urllib.parse import urljoin

from babel.dates import format_date
from babel.numbers import format_decimal
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
)
from flask_babel import get_locale

import database as db
import email_utils
from email_localization import gettext as recipient_gettext
from i18n import gettext as _
from i18n import localized_url
from locale_policy import resolve_locale
from security_extensions import rate_limit
from security_utils import validate_email_address
from site_release import accepting_applications, require_complete_site

verein_bp = Blueprint("verein", __name__, url_prefix="/verein")
verein_bp.before_request(require_complete_site)


@verein_bp.app_context_processor
def association_labels():
    def state_label(value):
        return {
            "unconfirmed": _("E-Mail noch nicht bestätigt"),
            "pending": _("Prüfung ausstehend"),
            "accepted": _("Aufgenommen"),
            "refused": _("Abgelehnt"),
            "active": _("Aktiv"),
            "resigned": _("Ausgetreten"),
            "terminated": _("Beendet"),
            "approved": _("Gutgeheissen"),
            "dismissed": _("Abgewiesen"),
            "received": _("Eingegangen, sofort wirksam"),
            "draft": _("Entwurf"),
            "scheduled": _("Bestätigt"),
            "cancelled": _("Abgesagt"),
        }.get(value, _("Unbekannt"))

    return {"verein_state_label": state_label}


@verein_bp.errorhandler(db.VereinUnavailable)
def association_unavailable(_error):
    return render_template(
        "verein/message.html",
        title=_("Dienst vorübergehend nicht verfügbar"),
        message=_("Bitte versuchen Sie es später erneut."),
    ), 503


@verein_bp.route("/mitglied/zugang", methods=["GET", "POST"])
@rate_limit("10 per hour")
def member_access():
    if request.method == "GET":
        return render_template("verein/member_access.html", csrf_token=csrf_token())
    require_csrf()
    valid, email, _error = validate_email_address(request.form.get("email", "").strip())
    if not valid:
        abort(400)
    if not email_utils.EMAIL_ENABLED:
        abort(503)
    try:
        access = db.request_verein_applicant_access(email)
        if access:
            link = localized_url(
                urljoin(
                    current_app.config["APP_BASE_URL"],
                    "/verein/mitglied/zugang/" + access["token"],
                ),
                access["locale"],
            )
            email_utils.send_email(
                email,
                recipient_gettext(
                    "Verein: persönlicher Zugang", locale=access["locale"]
                ),
                recipient_gettext(
                    "Öffnen Sie Ihren persönlichen Vereinszugang:\n\n%(link)s\n\nDer Link ist eine Stunde gültig und einmal verwendbar.",
                    link=link,
                    locale=access["locale"],
                ),
                private=True,
            )
    except db.VereinUnavailable:
        abort(503)
    return jsonify(
        message=_(
            "Falls die Adresse zu einem bestätigten Antrag gehört, wird ein Zugangslink versendet. Bei einem Versandproblem können Sie es erneut versuchen."
        )
    ), 202


@verein_bp.get("/mitglied/zugang/<token>")
def open_member_access(token):
    try:
        session["verein_applicant_session"] = db.consume_verein_applicant_access(token)
    except db.VereinDenied:
        abort(410)
    except db.VereinUnavailable:
        abort(503)
    session["verein_csrf"] = secrets.token_urlsafe(32)
    return redirect("/verein/mitglied", code=303)


def require_applicant():
    try:
        return db.get_verein_applicant_profile(session.get("verein_applicant_session"))
    except db.VereinDenied:
        abort(401)
    except db.VereinUnavailable:
        abort(503)


@verein_bp.get("/mitglied")
def member_profile():
    record = require_applicant()
    return render_template("verein/member.html", record=record, csrf_token=csrf_token())


@verein_bp.post("/mitglied/logout")
def applicant_logout():
    require_csrf()
    db.logout_verein_applicant(session.get("verein_applicant_session"))
    session.pop("verein_applicant_session", None)
    session.pop("verein_csrf", None)
    return redirect("/verein/mitglied/zugang", code=303)


@verein_bp.post("/mitglied/austritt")
def resign():
    require_applicant()
    require_csrf()
    try:
        db.submit_verein_member_request(
            session["verein_applicant_session"],
            "resignation",
            request.form.get("reason", ""),
        )
    except db.VereinDenied:
        abort(409)
    except db.VereinUnavailable:
        abort(503)
    return redirect("/verein/mitglied", code=303)


@verein_bp.post("/mitglied/beschwerde")
def appeal():
    require_applicant()
    require_csrf()
    try:
        db.submit_verein_member_request(
            session["verein_applicant_session"],
            "appeal",
            request.form.get("reason", ""),
        )
    except db.VereinDenied:
        abort(409)
    except db.VereinUnavailable:
        abort(503)
    return redirect("/verein/mitglied", code=303)


@verein_bp.post("/vorstand/gesuche/<identifier>")
def resolve_request(identifier):
    require_reviewer()
    require_csrf()
    try:
        db.resolve_verein_member_request(
            session["verein_session"],
            identifier,
            request.form.get("outcome", ""),
            request.form.get("reason", ""),
            request.form.get("evidence", ""),
        )
    except db.VereinDenied:
        abort(409)
    except db.VereinUnavailable:
        abort(503)
    return redirect("/verein/vorstand/antraege", code=303)


def csrf_token():
    if "verein_csrf" not in session:
        session["verein_csrf"] = secrets.token_urlsafe(32)
    return session["verein_csrf"]


def require_csrf():
    supplied = request.form.get("csrf_token", "")
    expected = session.get("verein_csrf", "")
    if (
        not expected
        or not supplied.isascii()
        or not hmac.compare_digest(supplied, expected)
    ):
        abort(403)


@verein_bp.route("/beitreten", methods=["GET", "POST"])
@rate_limit("10 per hour")
def join():
    if not accepting_applications():
        return render_template(
            "verein/message.html",
            title=_("Mitgliedschaft"),
            message=_(
                "Die Aufnahme öffnet nach der tatsächlichen Gründung und der Freigabe der Datenschutzangaben."
            ),
        ), 503
    if request.method == "GET":
        return render_template("verein/join.html", csrf_token=csrf_token())
    require_csrf()
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    affiliation = request.form.get("affiliation", "").strip()
    valid, email, _error = validate_email_address(email)
    if (
        not valid
        or not 2 <= len(name) <= 120
        or len(affiliation) > 1000
        or any(
            request.form.get(key) != "yes" for key in ("adult", "independent", "terms")
        )
    ):
        return jsonify(
            error=_("Bitte prüfen Sie Name, E-Mail und die Aufnahmebedingungen.")
        ), 400
    # The legacy SMTP helper simulates delivery if disabled. Intake must not.
    if not email_utils.EMAIL_ENABLED:
        return jsonify(error=_("Der E-Mail-Versand ist derzeit nicht verfügbar.")), 503
    try:
        token = db.submit_verein_application(
            email=email.casefold(),
            name=name,
            affiliation=affiliation,
            locale=str(get_locale()),
            privacy_version=current_app.config["VEREIN_PRIVACY_VERSION"],
        )
        if token:
            link = localized_url(
                urljoin(
                    current_app.config["APP_BASE_URL"], "/verein/bestaetigen/" + token
                )
            )
            sent = email_utils.send_email(
                email,
                _("Verein: E-Mail bestätigen"),
                _(
                    "Bestätigen Sie Ihre E-Mail für den Mitgliedsantrag:\n\n%(link)s\n\nDie Bestätigung ist noch keine Aufnahme. Der Vorstand prüft den Antrag.",
                    link=link,
                ),
                private=True,
            )
            if not sent:
                return jsonify(
                    error=_(
                        "Der Antrag ist gespeichert. Bitte versuchen Sie den E-Mail-Versand erneut."
                    )
                ), 503
    except db.VereinUnavailable:
        abort(503)
    return jsonify(
        status="pending_confirmation", message=_("Prüfen Sie Ihr E-Mail-Postfach.")
    ), 202


@verein_bp.get("/bestaetigen/<token>")
@rate_limit("30 per hour")
def confirm(token):
    if not accepting_applications():
        abort(503)
    try:
        db.confirm_verein_application(token)
    except db.VereinDenied:
        abort(410)
    except db.VereinUnavailable:
        abort(503)
    return render_template(
        "verein/message.html",
        title=_("E-Mail bestätigt"),
        message=_(
            "Ihr Antrag wartet auf die Prüfung durch den Vorstand. Sie sind noch kein Mitglied."
        ),
    )


@verein_bp.get("/vorstand/antraege")
def applications():
    require_reviewer()
    status = request.args.get("status", "pending")
    page = request.args.get("page", 1, type=int)
    try:
        records = db.list_verein_applications(
            session["verein_session"], status=status, page=page
        )
    except db.VereinDenied:
        abort(400)
    return render_template(
        "verein/queue.html",
        applications=records,
        filter_status=status,
        page=page,
        member_requests=db.list_verein_member_requests(session["verein_session"]),
        csrf_token=csrf_token(),
    )


@verein_bp.post("/vorstand/benachrichtigungen")
def deliver_notifications():
    require_reviewer()
    require_csrf()
    token = session["verein_session"]
    if not email_utils.EMAIL_ENABLED:
        abort(503)
    failed = False
    try:
        for notice in db.get_verein_notifications(token):
            locale = resolve_locale(notice["preferred_locale"], extended=True)
            if notice["kind"] == "accepted":
                message = recipient_gettext(
                    "Ihr Mitgliedsantrag wurde angenommen. Die Mitgliedschaft beginnt mit der Aufnahme. Der Jahresbeitrag von CHF 50 ist am %(due_on)s fällig. Zahlungsangaben und Rückfragen: hallo@openleg.ch.",
                    due_on=format_date(notice["due_on"], format="long", locale=locale),
                    locale=notice["preferred_locale"],
                )
            elif notice["kind"] == "refused":
                message = recipient_gettext(
                    "Ihr Mitgliedsantrag wurde abgelehnt. Begründung: %(reason)s\n\nSie können bei der Mitgliederversammlung Beschwerde einlegen. Kontakt: hallo@openleg.ch.",
                    reason=notice["decision_reason"],
                    locale=notice["preferred_locale"],
                )
            elif notice["kind"] == "reminder":
                message = recipient_gettext(
                    "Für das Beitragsjahr ab %(period)s sind CHF %(amount)s offen. Bitte zahlen Sie innerhalb von weiteren 60 Tagen ab dieser Erinnerung. Danach kann der Vorstand einen begründeten Entscheid zur Beendigung treffen. Sie können Beschwerde einlegen. Kontakt: hallo@openleg.ch.",
                    period=format_date(
                        notice["period_start"], format="long", locale=locale
                    ),
                    amount=format_decimal(
                        notice["outstanding_chf"], format="#,##0.00", locale=locale
                    ),
                    locale=notice["preferred_locale"],
                )
            elif notice["kind"] == "renewal":
                message = recipient_gettext(
                    "Für Ihr Beitragsjahr ab %(period)s beträgt der Jahresbeitrag CHF 50 und ist am %(due_on)s fällig. Ihren Beitragsstand sehen Sie über den persönlichen Vereinszugang. Kontakt: hallo@openleg.ch.",
                    period=format_date(
                        notice["period_start"], format="long", locale=locale
                    ),
                    due_on=format_date(notice["due_on"], format="long", locale=locale),
                    locale=notice["preferred_locale"],
                )
            elif notice["kind"] == "resignation_received":
                message = recipient_gettext(
                    "Ihre Austrittserklärung ist eingegangen. Der Austritt ist mit Eingang wirksam und benötigt keine Bewilligung des Vorstands. Datum und Beitragsstand sehen Sie über Ihren persönlichen Vereinszugang. Die Erklärung löscht nicht automatisch Buchungs- und Entscheidnachweise. Kontakt: hallo@openleg.ch.",
                    locale=notice["preferred_locale"],
                )
            elif notice["kind"] == "terminated":
                message = recipient_gettext(
                    "Der Vorstand hat Ihre Mitgliedschaft beendet. Begründung: %(reason)s\n\nSie können bei der Mitgliederversammlung Beschwerde einlegen. Kontakt: hallo@openleg.ch.",
                    reason=notice["decision_reason"],
                    locale=notice["preferred_locale"],
                )
            elif notice["kind"] in {
                "appeal_approved",
                "appeal_dismissed",
                "resignation_approved",
                "resignation_dismissed",
            }:
                message = recipient_gettext(
                    "Zu Ihrem Gesuch wurde ein Entscheid erfasst: %(reason)s. Den aktuellen Status, die Beitragsfrist und Ihre Gesuche sehen Sie über Ihren persönlichen Zugang unter %(access_url)s. Kontakt: hallo@openleg.ch.",
                    reason=notice["decision_reason"],
                    access_url=urljoin(
                        current_app.config["APP_BASE_URL"], "/verein/mitglied/zugang"
                    ),
                    locale=notice["preferred_locale"],
                )
            else:
                abort(409)
            sent = email_utils.send_email(
                notice["email"],
                recipient_gettext(
                    "Verein: Mitgliedschaft und Jahresbeitrag",
                    locale=notice["preferred_locale"],
                ),
                message,
                private=True,
            )
            db.record_verein_delivery(token, str(notice["id"]), sent)
            failed = failed or not sent
    except db.VereinUnavailable:
        abort(503)
    if failed:
        abort(503)
    return redirect("/verein/vorstand/antraege", code=303)


@verein_bp.post("/vorstand/beitraege/erneuern")
def renew_contributions():
    require_reviewer()
    require_csrf()
    count = db.renew_verein_dues(session["verein_session"])
    flash(
        _(
            "%(count)s neue Beitragsjahre erfasst. Benachrichtigungen werden erst über den Versand ausgelöst.",
            count=count,
        )
    )
    return redirect("/verein/vorstand/antraege", code=303)


def require_reviewer():
    try:
        return db.get_verein_reviewer(session.get("verein_session"))
    except db.VereinDenied:
        abort(401)
    except db.VereinUnavailable:
        abort(503)


@verein_bp.route("/vorstand/login", methods=["GET", "POST"])
@rate_limit("10 per hour")
def board_login():
    if request.method == "GET":
        return render_template("verein/login.html", csrf_token=csrf_token())
    require_csrf()
    email = request.form.get("email", "")
    password = request.form.get("password", "")
    if len(email) > 320 or len(password) > 1024:
        abort(401)
    try:
        token = db.login_verein_reviewer(email, password)
    except db.VereinDenied:
        abort(401)
    except db.VereinUnavailable:
        abort(503)
    session["verein_session"] = token
    session["verein_csrf"] = secrets.token_urlsafe(32)
    return redirect("/verein/vorstand/antraege", code=303)


@verein_bp.post("/vorstand/logout")
def board_logout():
    require_csrf()
    try:
        db.logout_verein_reviewer(session.get("verein_session"))
    except db.VereinUnavailable:
        abort(503)
    session.pop("verein_session", None)
    session.pop("verein_csrf", None)
    return redirect("/verein/vorstand/login", code=303)


@verein_bp.route("/vorstand/antraege/<identifier>", methods=["GET", "POST"])
def application_detail(identifier):
    require_reviewer()
    token = session["verein_session"]
    try:
        if request.method == "POST":
            require_csrf()
            db.decide_verein_application(
                token,
                identifier,
                request.form.get("decision", ""),
                request.form.get("reason", ""),
            )
            return redirect("/verein/vorstand/antraege/" + identifier, code=303)
        record = db.get_verein_application(token, identifier)
    except db.VereinDenied:
        abort(409 if request.method == "POST" else 404)
    except db.VereinUnavailable:
        abort(503)
    return render_template(
        "verein/application.html", record=record, csrf_token=csrf_token()
    )


@verein_bp.post("/vorstand/antraege/<identifier>/reminder")
def remind_member(identifier):
    require_reviewer()
    require_csrf()
    try:
        db.request_verein_reminder(
            session["verein_session"],
            identifier,
            dues_id=request.form.get("dues_id") or None,
        )
    except db.VereinDenied:
        abort(409)
    except db.VereinUnavailable:
        abort(503)
    return redirect("/verein/vorstand/antraege/" + identifier, code=303)


@verein_bp.post("/vorstand/antraege/<identifier>/payment")
def record_received_payment(identifier):
    require_reviewer()
    require_csrf()
    if request.form.get("verified") != "yes":
        abort(400)
    try:
        db.record_verein_payment(
            session["verein_session"],
            identifier,
            amount_chf=request.form.get("amount_chf", ""),
            source=request.form.get("source", ""),
            reference=request.form.get("reference", ""),
            evidence=request.form.get("evidence", ""),
            dues_id=request.form.get("dues_id") or None,
        )
    except db.VereinDenied:
        abort(409)
    return redirect("/verein/vorstand/antraege/" + identifier, code=303)


@verein_bp.post("/vorstand/antraege/<identifier>/terminate")
def terminate_member(identifier):
    require_reviewer()
    require_csrf()
    try:
        db.terminate_verein_membership(
            session["verein_session"],
            identifier,
            request.form.get("reason", ""),
            dues_id=request.form.get("dues_id") or None,
        )
    except db.VereinDenied:
        abort(409)
    except db.VereinUnavailable:
        abort(503)
    return redirect("/verein/vorstand/antraege/" + identifier, code=303)
