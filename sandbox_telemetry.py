# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bounded local sample source using ordinary telemetry enrollment and ingestion."""

import fcntl
import hmac
import json
import os
import secrets
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from flask import Blueprint, abort, redirect, render_template, request, session
from psycopg2 import Error as DatabaseError

import database as db
from dashboard_routes import (
    _dashboard_csrf_token,
    _require_dashboard_csrf,
    _require_dashboard_session,
)
from sandbox_metering_fixtures import COMMUNITY
from telemetry import TelemetryError
from translation_markers import gettext_noop as _N

ERRORS = {
    "local_source_paused_or_credential_missing": _N(
        "Die lokale Quelle ist pausiert oder hat keinen gültigen Schlüssel. Setzen Sie sie fort oder erneuern Sie den Schlüssel."
    ),
    "local_source_has_no_sample": _N(
        "Veröffentlichen Sie zuerst ein Muster, bevor Sie es wiederholen."
    ),
    "invalid_telemetry_credential": _N(
        "Der Geräteschlüssel ist widerrufen oder nicht mehr gültig. Erneuern Sie ihn als Eigentümer."
    ),
}


def _directory():
    """Locate the current worker's private local-source state directory."""
    directory = Path(os.environ["OPENLEG_SANDBOX_DIRECTORY"]).resolve()
    if os.environ.get("OPENLEG_SANDBOX_WORKER") != "1" or directory != Path.cwd():
        raise RuntimeError("local telemetry requires a village worker")
    target = directory / "simulation-telemetry"
    target.mkdir(mode=0o700, exist_ok=True)
    return target


def _read(path):
    """Read saved local-source state or return its initial paused flag."""
    if not path.exists():
        return {"paused": False}
    return json.loads(path.read_text())


@contextmanager
def _source(installation):
    """Serialize this installation's local credential and replay publication."""
    directory = _directory()
    path = directory / (installation + ".json")
    descriptor = os.open(
        directory / (installation + ".lock"), os.O_WRONLY | os.O_CREAT, 0o600
    )
    with os.fdopen(descriptor, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = _read(path)
        yield state
        descriptor, temporary = tempfile.mkstemp(dir=directory, suffix=".pending")
        try:
            with os.fdopen(descriptor, "w") as output:
                json.dump(state, output)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)


def _owned(actor, installation):
    """Resolve an installation belonging to the current household."""
    row = next(
        (
            row
            for row in db.list_telemetry_installations(COMMUNITY, actor)
            if row["id"] == installation
        ),
        None,
    )
    if not row:
        abort(404)
    return row


def _issue_display(state, installation, credential):
    """Queue a private one-use display; put only its opaque nonce in the cookie."""
    nonce = secrets.token_urlsafe(24)
    state["pending_display"] = {"nonce": nonce, "credential": credential}
    session["sandbox_telemetry_display"] = {
        "installation": installation,
        "nonce": nonce,
    }


def _success(result):
    """Keep an action receipt for the next telemetry page."""
    session["sandbox_telemetry_result"] = result
    return redirect("/sandbox/telemetry", code=303)


def _present(*, error=None, status=200):
    """Render owned installations and consume any one-use credential display."""
    actor = _require_dashboard_session()
    rows = db.list_telemetry_installations(COMMUNITY, actor)
    credential = None
    receipt = session.pop("sandbox_telemetry_display", None)
    if receipt and receipt["installation"] in {row["id"] for row in rows}:
        with _source(receipt["installation"]) as state:
            pending = state.get("pending_display")
            if pending and hmac.compare_digest(pending["nonce"], receipt["nonce"]):
                credential = state.pop("pending_display")["credential"]
    result = session.pop("sandbox_telemetry_result", None)
    for row in rows:
        row["paused"] = _read(_directory() / (row["id"] + ".json"))["paused"]
    response = render_template(
        "sandbox/telemetry.html",
        installations=rows,
        credential=credential,
        result=result,
        error=ERRORS.get(
            error,
            _N(
                "Die Aktion wurde abgelehnt. Prüfen Sie Anmeldung, Eigentum, Freigabe und Geräteschlüssel."
            ),
        )
        if error
        else None,
        csrf_token=_dashboard_csrf_token(),
        community_id=COMMUNITY,
    )
    return response, status, {"Cache-Control": "no-store"}


def _samples(row, fault):
    """Build synthetic power and fault samples after the stored highwater mark."""
    observed = datetime.now(UTC).isoformat()
    samples = []
    for offset, metric in enumerate(("power", "device_fault"), 1):
        sequence = row["highwater"] + offset
        samples.append(
            {
                "sample_id": f"simulation-{sequence}",
                "sequence": sequence,
                "device_id": row["device_id"],
                "source_id": row["source_id"],
                "cadence_seconds": row["cadence_seconds"],
                "metric": metric,
                "unit": "W" if metric == "power" else "state",
                "value": "2500" if metric == "power" else str(int(fault)),
                "measurement_location": "pv_inverter"
                if metric == "power"
                else "device",
                "direction": "generation" if metric == "power" else "status",
                "quality": "measured",
                "observed_at": observed,
            }
        )
    return {"schema_version": "telemetry/1", "samples": samples}


def install(web):
    """Install controls only in the disposable worker with no device network."""
    if os.environ.get("OPENLEG_SANDBOX_WORKER") != "1":
        raise RuntimeError("local telemetry requires a village worker")

    bp = Blueprint("sandbox_telemetry", __name__)

    @bp.errorhandler(TelemetryError)
    def local_telemetry_error(error):
        """Render safe telemetry errors without caching credential responses."""
        try:
            return _present(error=error.code, status=error.status)
        except TelemetryError:
            return error.code, error.status, {"Cache-Control": "no-store"}

    @bp.get("/sandbox/telemetry")
    def sandbox_telemetry():
        """Show the current household's local telemetry exercise."""
        try:
            return _present()
        except DatabaseError:
            abort(503)

    @bp.post("/sandbox/telemetry/enroll")
    def sandbox_telemetry_enroll():
        """Enroll a confirmed local source and queue its credential display."""
        actor = _require_dashboard_session()
        _require_dashboard_csrf()
        if request.form.get("confirm") != "yes":
            abort(400)
        enrolled = db.enroll_telemetry_installation(
            COMMUNITY,
            actor,
            {
                "name": "SIMULATION SONNWIL local samples",
                "device_id": "simulation-" + actor,
                "source_id": "simulation-local-source",
                "cadence_seconds": 1,
            },
        )
        with _source(enrolled["installation"]["id"]) as state:
            state["credential"] = enrolled["credential"]
            _issue_display(
                state, enrolled["installation"]["id"], enrolled["credential"]
            )
        return _success({"enrolled": enrolled["installation"]["id"]})

    @bp.post("/sandbox/telemetry/<uuid:installation>/<action>")
    def sandbox_telemetry_control(installation, action):
        """Apply an authorized local-source action under its publication lock."""
        actor = _require_dashboard_session()
        _require_dashboard_csrf()
        installation = str(installation)
        _owned(actor, installation)
        if request.form.get("confirm") != "yes":
            abort(400)
        credential = None
        result = None
        with _source(installation) as state:
            # Read again after the local source lock to bind sequences to current storage.
            row = _owned(actor, installation)
            if action in {"pause", "resume"}:
                state["paused"] = action == "pause"
                result = {"paused": state["paused"]}
            elif action in {"rotate", "revoke"}:
                result = db.change_telemetry_installation(
                    COMMUNITY, installation, actor, action
                )
                if action == "rotate":
                    state["previous_credential"] = state.get("credential", "")
                    credential = result.pop("credential")
                    state["credential"] = credential
                    _issue_display(state, installation, credential)
            elif action == "test-old-credential":
                if not state.get("previous_credential"):
                    raise TelemetryError("local_source_has_no_previous_credential", 409)
                db.charge_telemetry_ingestion(
                    COMMUNITY, installation, state["previous_credential"]
                )
                result = db.ingest_telemetry_samples(
                    COMMUNITY,
                    installation,
                    state["previous_credential"],
                    _samples(row, False),
                )
            elif action in {"share", "unshare"}:
                viewer = request.form.get("viewer", "")
                if (
                    viewer
                    not in {"sonnwil-producer", "sonnwil-resident", "sonnwil-neighbour"}
                    or viewer == actor
                ):
                    abort(400)
                result = db.change_telemetry_installation(
                    COMMUNITY,
                    installation,
                    actor,
                    action,
                    {"viewer_building_id": viewer},
                )
            elif action in {"publish", "fault", "clear", "replay"}:
                if state["paused"] or not state.get("credential"):
                    raise TelemetryError(
                        "local_source_paused_or_credential_missing", 409
                    )
                payload = (
                    state.get("last_payload")
                    if action == "replay"
                    else _samples(row, action == "fault")
                )
                if not payload:
                    raise TelemetryError("local_source_has_no_sample", 409)
                db.charge_telemetry_ingestion(
                    COMMUNITY, installation, state["credential"]
                )
                result = db.ingest_telemetry_samples(
                    COMMUNITY, installation, state["credential"], payload
                )
                state["last_payload"] = payload
            else:
                abort(404)
        return _success(result)

    web.register_blueprint(bp)
