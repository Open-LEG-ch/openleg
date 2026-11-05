# SPDX-License-Identifier: AGPL-3.0-or-later
"""A workspace-local receiver for the real operator outbox/signature path."""

import json
import os
import tempfile
from pathlib import Path
from uuid import UUID

from flask import abort, redirect, render_template, request, session
from psycopg2 import Error as DatabaseError

import community_access
import database as db
import operator_api
from dashboard_routes import (
    _dashboard_csrf_token,
    _require_dashboard_csrf,
    _require_dashboard_session,
)
from sandbox_metering_fixtures import COMMUNITY


def _authorize():
    """Require current confirmed administrator authority in this village."""
    actor = _require_dashboard_session()
    community = db.fetch_community_with_members(COMMUNITY, actor)
    member = next(
        (
            row
            for row in (community or {}).get("members", [])
            if row["building_id"] == actor
        ),
        None,
    )
    if (
        not member
        or member.get("status") != "confirmed"
        or not community_access.is_administrator(member)
    ):
        abort(403)
    return actor


def _directory():
    """Locate the current worker's private webhook capture directory."""
    root = Path(os.environ["OPENLEG_SANDBOX_DIRECTORY"]).resolve()
    if os.environ.get("OPENLEG_SANDBOX_WORKER") != "1" or root != Path.cwd():
        raise RuntimeError("local webhook capture requires a village worker")
    directory = root / "operator-captures"
    directory.mkdir(mode=0o700, exist_ok=True)
    return directory


def _captures():
    """Read the most recent local webhook receipts."""
    paths = sorted(
        _directory().glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True
    )[:100]
    return [json.loads(path.read_text()) for path in paths]


def _transport(mode):
    """Build a local receiver that verifies signatures and records simulated responses."""

    def capture(url, body, headers, _timeout):
        """Verify one scoped delivery and atomically save its local receipt."""
        if url != operator_api.SANDBOX_WEBHOOK_URL or len(body) > 65536:
            return 400
        try:
            delivery_id = str(UUID(headers["OpenLEG-Delivery"]))
            payload = json.loads(body)
        except (ValueError, KeyError, UnicodeError):
            return 400
        if not isinstance(payload, dict) or payload.get("community_id") != COMMUNITY:
            return 400
        context = db.get_operator_webhook_delivery_context(COMMUNITY, delivery_id)
        if not context:
            return 404
        secret = operator_api._webhook_secret(
            context["client_id"], version=context["webhook_secret_version"]
        )
        signature = headers.get("OpenLEG-Signature", "")
        verified = operator_api.verify_webhook_signature(body, signature, secret)
        status = (503 if mode == "fail" else 204) if verified else 401
        receipt = {
            "delivery_id": delivery_id,
            "attempt": context["attempt_count"] + 1,
            "body": body.decode(),
            "signature": signature,
            "verified": verified,
            "response_status": status,
        }
        directory = _directory()
        descriptor, temporary = tempfile.mkstemp(dir=directory, suffix=".pending")
        try:
            with os.fdopen(descriptor, "w") as output:
                json.dump(receipt, output)
            os.replace(
                temporary, directory / f"{delivery_id}-{receipt['attempt']}.json"
            )
        finally:
            Path(temporary).unlink(missing_ok=True)
        return status

    return capture


def install(web):
    """Expose no local sink or console outside a disposable worker."""
    if os.environ.get("OPENLEG_SANDBOX_WORKER") != "1":
        raise RuntimeError("operator console requires a village worker")

    @web.get("/sandbox/operator")
    def sandbox_operator():
        """Show safe credentials, scoped deliveries and local capture receipts."""
        _authorize()
        try:
            credentials = [
                operator_api._safe_credential(row)
                for row in db.list_operator_api_clients(COMMUNITY)
            ]
            deliveries = db.list_operator_webhook_deliveries(COMMUNITY)
            captures = _captures()
        except (DatabaseError, OSError, ValueError):
            abort(503)
        return render_template(
            "sandbox/operator.html",
            community_id=COMMUNITY,
            credentials=credentials,
            deliveries=deliveries,
            captures=captures,
            csrf_token=_dashboard_csrf_token(),
            webhook_url=operator_api.SANDBOX_WEBHOOK_URL,
            result=session.pop("sandbox_operator_result", None),
        )

    @web.get("/sandbox/operator/captures")
    def sandbox_operator_captures():
        """Return capture receipts after checking administrator authority."""
        _authorize()
        return {"captures": _captures()}

    @web.post("/sandbox/operator/dispatch")
    def sandbox_operator_dispatch():
        """Dispatch one confirmed local delivery through normal outbox commands."""
        _authorize()
        _require_dashboard_csrf()
        mode = request.form.get("mode")
        if mode not in {"fail", "deliver"} or request.form.get("confirm") != "yes":
            abort(400)
        session["sandbox_operator_result"] = operator_api.dispatch_pending_webhooks(
            transport=_transport(mode),
            batch_size=1,
            max_attempts=1 if mode == "fail" else 5,
            community_id=COMMUNITY,
        )
        return redirect("/sandbox/operator", code=303)
