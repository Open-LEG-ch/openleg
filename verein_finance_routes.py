# SPDX-License-Identifier: AGPL-3.0-or-later
"""Protected report preparation and explicitly redacted public financial reports."""

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from babel.numbers import format_decimal
from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
)
from flask_babel import get_locale

import database as db
import verein_finance
from i18n import format_date_label
from i18n import gettext as _
from site_release import require_complete_site
from verein_routes import csrf_token, require_csrf, require_reviewer

finance_bp = Blueprint("verein_finance", __name__)
finance_bp.before_request(require_complete_site)


@finance_bp.errorhandler(db.VereinUnavailable)
def unavailable(_error):
    return render_template(
        "verein/message.html",
        title=_("Dienst vorübergehend nicht verfügbar"),
        message=_("Bitte versuchen Sie es später erneut."),
    ), 503


@finance_bp.app_context_processor
def finance_labels():
    return {
        "finance_date": lambda value: format_date_label(
            (
                datetime.fromisoformat(value)
                if "T" in value
                else date.fromisoformat(value)
            )
            if isinstance(value, str)
            else value
        ),
        "finance_amount": lambda cents: format_decimal(
            Decimal(cents) / 100, format="#,##0.00", locale=str(get_locale())
        ),
        "finance_kind": lambda kind: {
            "receipt": _("Geldeingang"),
            "refund": _("Rückzahlung"),
            "fee": _("Gebühr"),
            "expense": _("Ausgabe"),
            "transfer": _("Interner Transfer"),
            "allocation": _("Umbuchung zwischen Fonds"),
        }.get(kind, kind),
        "finance_category": lambda category: {
            "starting_contribution": _("Anfangseinlage"),
            "membership": _("Mitgliedsbeitrag"),
            "donation": _("Spende"),
            "grant": _("Förderbeitrag"),
            "held": _("Zur Prüfung zurückgehalten"),
            "refund": _("Rückzahlung"),
            "provider_fee": _("Anbietergebühr"),
            "bank_fee": _("Bankgebühr"),
            "hosting": _("Betriebskosten"),
            "administration": _("Administration"),
            "community": _("Community"),
            "development": _("Entwicklung"),
            "other": _("Weitere Kosten"),
            "internal": _("Interne Bewegung"),
        }.get(category, category),
        "finance_fund": lambda fund: {
            "general": _("Freie Mittel"),
            "reserve": _("Reserve"),
            "restricted": _("Zweckgebundene Mittel"),
            "held": _("Zur Prüfung zurückgehalten"),
        }.get(fund, fund),
    }


@finance_bp.route("/verein/vorstand/finanzen", methods=["GET", "POST"])
def prepare():
    require_reviewer()
    error = None
    form = request.form
    if request.method == "POST":
        require_csrf()
        try:
            identifier = db.prepare_verein_finance_report(
                session["verein_session"], request.form
            )
            return redirect("/verein/vorstand/finanzen/" + identifier, code=303)
        except db.VereinDenied:
            error = _(
                "Der Bericht wurde nicht gespeichert. Prüfen Sie CSV-Spalten, Quellen, Beträge und Datum, alle Konten und Fonds sowie die Übereinstimmung der Anfangs- und Schlussbestände. Für eine neue Version ist eine öffentliche Begründung erforderlich. Bereits veröffentlichte Belege dürfen nicht verschwinden. Ihre Eingaben bleiben erhalten."
            )
    if request.method == "GET" and request.args.get("copy"):
        source = db.get_verein_finance_review(
            session["verein_session"], request.args["copy"]
        )
        if source is None:
            abort(404)
        form = {
            key: source["private_input"][key]
            for key in ("quarter", "scope_evidence", "opening_evidence")
        }
        for name, columns in verein_finance.COLUMNS.items():
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=columns.split(","))
            writer.writeheader()
            writer.writerows(source["private_input"][name])
            form[name] = output.getvalue()
    reports = db.get_verein_finance_review(session["verein_session"])
    return render_template(
        "verein/finance_edit.html",
        reports=reports,
        form=form,
        columns=verein_finance.COLUMNS,
        error=error,
        csrf_token=csrf_token(),
    ), 409 if error else 200


@finance_bp.get("/verein/vorstand/finanzen/vorlage/<name>.csv")
def csv_template(name):
    require_reviewer()
    if name not in verein_finance.COLUMNS:
        abort(404)
    return Response(
        verein_finance.COLUMNS[name] + "\r\n",
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
    )


@finance_bp.get("/verein/vorstand/finanzen/<identifier>")
def review(identifier):
    require_reviewer()
    report = db.get_verein_finance_review(session["verein_session"], identifier)
    if report is None:
        abort(404)
    return render_template(
        "verein/finance_review.html", report=report, csrf_token=csrf_token(), error=None
    )


@finance_bp.post("/verein/vorstand/finanzen/<identifier>/freigeben")
def publish(identifier):
    require_reviewer()
    require_csrf()
    try:
        if any(
            request.form.get(name) != "yes"
            for name in ("sources_checked", "scope_checked", "redaction_checked")
        ):
            raise db.VereinDenied("Confirm each actual check")
        quarter = db.publish_verein_finance_report(
            session["verein_session"],
            identifier,
            request.form.get("review_evidence", ""),
        )
        return redirect("/verein/finanzen/" + quarter, code=303)
    except db.VereinDenied:
        report = db.get_verein_finance_review(session["verein_session"], identifier)
        if report is None:
            abort(404)
        return render_template(
            "verein/finance_review.html",
            report=report,
            csrf_token=csrf_token(),
            error=_(
                "Keine Freigabe. Eine andere aktive berechtigte Person muss den aktuellen Stand anhand der Originalquellen, des vollständigen Umfangs und der bereinigten öffentlichen Angaben prüfen und die tatsächliche Freigabe belegen."
            ),
        ), 409


def _selection(quarter):
    if not re.fullmatch(r"20[0-9]{2}-Q[1-4]", quarter):
        abort(404)
    version = request.args.get("version")
    if version is not None:
        if (
            not version.isascii()
            or not version.isdigit()
            or len(version) > 7
            or int(version) < 1
        ):
            abort(404)
        version = int(version)
    report = db.get_published_verein_finance(quarter, version)
    if report is None:
        abort(404)
    return report


@finance_bp.get("/verein/finanzen/<quarter>.json")
def report_json(quarter):
    return jsonify(_selection(quarter))


@finance_bp.get("/verein/finanzen")
@finance_bp.get("/verein/finanzen/<quarter>")
def public(quarter=None):
    report = _selection(quarter) if quarter else None
    reports = db.get_published_verein_finance() if not quarter else []
    now = datetime.now(ZoneInfo("Europe/Zurich"))
    ordinal = now.year * 4 + (now.month - 1) // 3 - 1
    last_completed = f"{ordinal // 4}-Q{ordinal % 4 + 1}"
    return render_template(
        "release/finance.html",
        page_title=_("Vereinsfinanzen"),
        page_description=_(
            "Vierteljährliche Berichte über alle kontrollierten Mittel des Vereins, mit Quellenabgleich und unabhängiger zweiter Prüfung."
        ),
        report=report,
        reports=reports,
        history=db.get_verein_finance_history(quarter) if quarter else [],
        delayed=bool(reports and reports[0]["quarter"] < last_completed),
        locale=str(get_locale()),
        site_url=current_app.config["SITE_URL"],
    )
