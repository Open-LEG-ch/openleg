# SPDX-License-Identifier: AGPL-3.0-or-later
"""Self-guided questions over ordinary member and keyed operator commands."""

import io
import os
import secrets

from flask import abort, redirect, render_template, request, send_file
from psycopg2 import Error as DatabaseError

import community_access
import database as db
from dashboard_routes import (
    _dashboard_csrf_token,
    _require_dashboard_csrf,
    _require_dashboard_session,
)
from sandbox_metering_fixtures import COMMUNITY
from translation_markers import gettext_noop as _N


def _scope():
    """Resolve the current member and their billing response authority."""
    actor = _require_dashboard_session()
    try:
        community = db.fetch_community_with_members(COMMUNITY, actor)
    except (db.BillingStoreError, DatabaseError):
        abort(503)
    member = next(
        (
            row
            for row in (community or {}).get("members", [])
            if row["building_id"] == actor
        ),
        None,
    )
    return actor, community_access.allows(member, community_access.PREPARE_BILLING)


def _cases(actor, operator):
    """List cases within the member or operator's current scope."""
    return db.list_invoice_queries(
        None, **({"community_id": COMMUNITY} if operator else {"participant_id": actor})
    )


def _present(error=None, status=200):
    """Render scoped question histories and their operator events."""
    actor, operator = _scope()
    try:
        cases = _cases(actor, operator)
        ids = {str(case["id"]) for case in cases}
        events = [
            event
            for event in db.list_community_operator_events(COMMUNITY)
            if event["event_type"] == "invoice.case.updated"
            and event["aggregate_id"] in ids
        ]
    except (db.BillingStoreError, DatabaseError):
        abort(503)
    return render_template(
        "sandbox/questions.html",
        cases=cases,
        events=events,
        operator=operator,
        csrf_token=_dashboard_csrf_token(),
        key=secrets.token_urlsafe(24),
        error=error,
    ), status


def install(web):
    """Keep fictional guidance and session-backed API adapters worker-only."""
    if os.environ.get("OPENLEG_SANDBOX_WORKER") != "1":
        raise RuntimeError("invoice-question simulation requires a village worker")

    @web.get("/sandbox/questions")
    def sandbox_questions():
        """Show question guidance for the current member or operator."""
        return _present()

    @web.get("/sandbox/questions/attachments/<int:message_id>")
    def sandbox_question_attachment(message_id):
        """Download an attachment only within the current case scope."""
        actor, operator = _scope()
        try:
            attachment = db.get_invoice_query_attachment(
                message_id,
                **(
                    {"community_id": COMMUNITY}
                    if operator
                    else {"participant_id": actor}
                ),
            )
        except (db.BillingStoreError, DatabaseError):
            abort(503)
        if not attachment:
            abort(404)
        response = send_file(
            io.BytesIO(bytes(attachment["attachment_data"])),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=attachment["attachment_filename"],
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @web.post("/sandbox/questions/<int:case_id>/respond")
    def sandbox_question_respond(case_id):
        """Apply a confirmed, keyed operator response through ordinary commands."""
        actor, operator = _scope()
        _require_dashboard_csrf()
        if not operator:
            abort(403)
        key = request.form.get("key", "")
        if request.form.get("confirm") != "yes" or not 1 <= len(key) <= 128:
            abort(400)
        try:
            result = db.respond_operator_invoice_case(
                case_id,
                COMMUNITY,
                actor,
                request.form.get("message", ""),
                request.form.get("status", ""),
                key,
            )
        except ValueError:
            return _present(
                _N(
                    "Dieser Statuswechsel oder diese Wiederholung ist nicht zulässig. Prüfen Sie den Fall und verwenden Sie für eine neue Antwort einen neuen Schlüssel."
                ),
                409,
            )
        except (db.BillingStoreError, DatabaseError):
            abort(503)
        if not result:
            abort(404)
        return redirect("/sandbox/questions", code=303)
