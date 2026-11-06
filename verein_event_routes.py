# SPDX-License-Identifier: AGPL-3.0-or-later
"""Published occurrence pages and a separate, authenticated board editor."""

from datetime import UTC, datetime

from babel.dates import format_datetime
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
import verein_events
from i18n import gettext as _
from i18n import localized_url
from site_release import require_complete_site
from verein_routes import csrf_token, require_csrf, require_reviewer

events_bp = Blueprint("verein_events", __name__)
events_bp.before_request(require_complete_site)


@events_bp.errorhandler(db.VereinUnavailable)
def unavailable(_error):
    return render_template(
        "verein/message.html",
        title=_("Dienst vorübergehend nicht verfügbar"),
        message=_("Bitte versuchen Sie es später erneut."),
    ), 503


def presentation(event):
    locale = str(get_locale())
    return {
        **event,
        **event["content"]["translations"][locale],
        "start_text": format_datetime(
            event["starts_at"],
            format="long",
            tzinfo=verein_events.ZURICH,
            locale=locale,
        ),
        "end_text": format_datetime(
            event["ends_at"], format="long", tzinfo=verein_events.ZURICH, locale=locale
        ),
        "past": event["ends_at"] <= datetime.now(UTC),
        "public_notes": event["notes"].get(locale)
        if event["notes_approved_at"] and event["notes"]
        else None,
    }


@events_bp.get("/veranstaltungen")
def index():
    events = [presentation(event) for event in db.list_published_verein_meetups()]
    return render_template(
        "release/events.html", events=events, site_url=current_app.config["SITE_URL"]
    )


@events_bp.get("/veranstaltungen/<slug>")
def detail(slug):
    event = db.list_published_verein_meetups(slug)
    if event is None:
        abort(404)
    return render_template(
        "release/event.html",
        event=presentation(event),
        site_url=current_app.config["SITE_URL"],
    )


@events_bp.get("/veranstaltungen/<slug>.ics")
def calendar(slug):
    event = db.list_published_verein_meetups(slug)
    if event is None:
        abort(404)
    url = localized_url(
        current_app.config["SITE_URL"] + "/veranstaltungen/" + slug, str(get_locale())
    )
    return Response(
        verein_events.calendar(event, str(get_locale()), url),
        mimetype="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="' + slug + '.ics"'},
    )


@events_bp.route("/verein/vorstand/treffen", methods=["GET", "POST"])
def edit():
    require_reviewer()
    token = session["verein_session"]
    error = None
    form = request.form
    if request.method == "POST":
        require_csrf()
        try:
            identifier = form.get("identifier") or None
            data = {
                key: form.get(key, "")
                for key in (
                    "slug",
                    "start",
                    "end",
                    "organiser",
                    "spoken_languages",
                    "join_state",
                    "join_url",
                    "status",
                )
            }
            data["translations"] = {
                locale: {
                    key: form.get(key + "_" + locale, "")
                    for key in ("title", "audience", "agenda")
                }
                for locale in verein_events.LOCALES
            }
            db.save_verein_meetup(
                token,
                data,
                identifier=identifier,
                version=int(form.get("version", "0")),
            )
            return redirect("/verein/vorstand/treffen", code=303)
        except (db.VereinDenied, ValueError):
            error = _(
                "Bitte prüfen Sie alle Angaben. Die Zeit muss in Europe/Zurich eindeutig sein, das Ende nach dem Beginn liegen. Alle vier Sprachen und für eine offene Anmeldung ein öffentlicher HTTPS-Link sind erforderlich. Bei einem veralteten Formular öffnen Sie den aktuellen Stand erneut."
            )
    events = db.list_verein_meetups_for_review(token)
    if request.method == "GET" and request.args.get("id"):
        selected = next(
            (item for item in events if str(item["id"]) == request.args["id"]), None
        )
        if selected is None:
            abort(404)
        form = {
            **selected["content"],
            "identifier": str(selected["id"]),
            "version": str(selected["version"]),
        }
        for key in ("start", "end"):
            form[key] = (
                datetime.fromisoformat(form[key])
                .astimezone(verein_events.ZURICH)
                .strftime("%Y-%m-%dT%H:%M")
            )
        for locale, values in selected["content"]["translations"].items():
            form.update({key + "_" + locale: value for key, value in values.items()})
    return render_template(
        "verein/events.html",
        events=events,
        form=form,
        error=error,
        csrf_token=csrf_token(),
    ), 409 if error else 200


@events_bp.post("/verein/vorstand/treffen/<uuid:identifier>/notizen")
def notes(identifier):
    require_reviewer()
    require_csrf()
    try:
        if request.form.get("action") == "withdraw":
            db.withdraw_verein_meetup_notes(
                session["verein_session"],
                str(identifier),
                request.form.get("evidence", ""),
                version=int(request.form.get("version", "0")),
            )
            return redirect("/verein/vorstand/treffen", code=303)
        db.publish_verein_meetup_notes(
            session["verein_session"],
            str(identifier),
            {
                locale: request.form.get("notes_" + locale, "")
                for locale in verein_events.LOCALES
            },
            request.form.get("evidence", ""),
            version=int(request.form.get("version", "0")),
        )
    except (db.VereinDenied, ValueError):
        events = db.list_verein_meetups_for_review(session["verein_session"])
        for event in events:
            if str(event["id"]) == str(identifier):
                event["notes_form"] = {
                    locale: request.form.get("notes_" + locale, "")
                    for locale in verein_events.LOCALES
                }
                event["notes_evidence_form"] = request.form.get("evidence", "")
        return render_template(
            "verein/events.html",
            events=events,
            form={},
            csrf_token=csrf_token(),
            error=_(
                "Die Notizen konnten nicht gespeichert werden. Prüfen Sie den aktuellen Stand, die Freigabereferenz und alle vier Sprachfassungen. Veröffentlicht werden können nur Notizen zu einem vergangenen, veröffentlichten Treffen. Ihr Entwurf bleibt unten erhalten; vergleichen Sie ihn vor dem erneuten Speichern mit der öffentlichen Seite."
            ),
        ), 409
    return redirect("/verein/vorstand/treffen", code=303)
