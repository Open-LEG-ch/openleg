# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bind installation-level archive operations to one visitor's scratch target."""

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

import psycopg2
from flask import Response, abort, redirect, render_template, request, session
from psycopg2.extras import RealDictCursor

import community_archive
from sandbox_runtime import RetirementConflict
from sandbox_urls import scope_page

MAX_ARCHIVE_BYTES = 50 * 1024 * 1024


class DestinationStore(community_archive.PostgresArchiveStore):
    """Use the existing restore transaction on a server-owned scratch database."""

    def __init__(self, villages, destination):
        self.dsn = villages.postgres.dsn
        self.database = destination["name"]

    @contextmanager
    def _connection(self):
        connection = psycopg2.connect(
            self.dsn, dbname=self.database, cursor_factory=RealDictCursor
        )
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def _snapshot(workspace, payload):
    descriptor, temporary = tempfile.mkstemp(
        dir=workspace["directory"], suffix=".pending"
    )
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
        os.replace(temporary, workspace["directory"] / "archive-candidate.json")
    finally:
        Path(temporary).unlink(missing_ok=True)


def install(web, villages, origin, proxy, expired):
    """Authorize against the source before granting narrow scratch authority."""

    @contextmanager
    def source(identifier):
        if session.get("workspace") != identifier:
            abort(410)
        with villages.use(identifier) as workspace:
            if not workspace:
                abort(410)
            yield workspace

    def authorize(workspace, body):
        response = proxy(workspace, "sandbox/archive/authorize", body)
        if response.status_code != 200:
            abort(response.status_code)
        return response.get_json()

    def current_destination(workspace):
        state = workspace.get("archive", {})
        destination = villages.get(state.get("destination"))
        if destination and destination.get("restore_parent") == workspace["id"]:
            return destination
        return None

    def target(workspace, identity):
        state = workspace.get("archive", {})
        if state and state["community_id"] != identity["community_id"]:
            abort(409)
        destination = current_destination(workspace)
        if not destination:
            try:
                destination = villages.create(
                    origin,
                    session.get("locale", "de_CH"),
                    owner="archive:" + workspace["id"],
                    initial_persona=None,
                    restore_parent=workspace["id"],
                )
            except RetirementConflict:
                abort(410)
            except (OverflowError, RuntimeError, OSError, psycopg2.Error):
                abort(503)
            workspace["archive"] = {
                **identity,
                "destination": destination["id"],
                "restored": False,
            }
        return destination

    def run(workspace, destination, *, dry_run):
        payload = (workspace["directory"] / "archive-candidate.json").read_bytes()
        if len(payload) > MAX_ARCHIVE_BYTES:
            abort(413)
        with villages.use(destination["id"]) as active:
            if not active or active.get("restore_parent") != workspace["id"]:
                abort(410)
            try:
                result = community_archive.restore_community_archive(
                    payload,
                    dry_run=dry_run,
                    store=DestinationStore(villages, active),
                )
            except (psycopg2.Error, community_archive.ArchiveError):
                abort(503)
        workspace["archive"]["result"] = result
        if result.get("restored"):
            workspace["archive"]["restored"] = True

    @web.get("/v/<identifier>/sandbox/archive")
    def archive_guide(identifier):
        with source(identifier) as workspace:
            response = proxy(workspace, "sandbox/archive/context", b"", method="GET")
            if response.status_code != 200:
                return response
            context = response.get_json()
            state = workspace.get("archive")
            prefix = "/v/" + identifier
            content = render_template(
                "sandbox/archive.html",
                **context,
                prefix=prefix,
                state=state,
                destination_available=bool(current_destination(workspace)),
                sandbox_mode=True,
                sandbox_account=context["account"],
            )
            return Response(scope_page(content, prefix, origin), mimetype="text/html")

    @web.post("/v/<identifier>/sandbox/archive/prepare")
    def archive_prepare(identifier):
        body = request.get_data()
        with source(identifier) as workspace:
            identity = authorize(workspace, body)
            variant = request.form.get("variant", "current")
            if variant not in {"current", "damaged"}:
                abort(400)
            response = proxy(
                workspace,
                "leg/community/" + identity["community_id"] + "/archive",
                b"",
                method="GET",
            )
            if response.status_code != 200:
                return response
            payload = response.get_data()
            if len(payload) > MAX_ARCHIVE_BYTES:
                abort(413)
            if variant == "damaged":
                archive = json.loads(payload)
                archive["manifest"]["hashes"]["communities"] = "sha256:" + "0" * 64
                payload = json.dumps(archive).encode()
            destination = target(workspace, identity)
            _snapshot(workspace, payload)
            run(workspace, destination, dry_run=True)
        return redirect(f"/v/{identifier}/sandbox/archive", code=303)

    @web.post("/v/<identifier>/sandbox/archive/restore")
    def archive_restore(identifier):
        body = request.get_data()
        with source(identifier) as workspace:
            identity = authorize(workspace, body)
            state = workspace.get("archive", {})
            if (
                state.get("community_id") != identity["community_id"]
                or request.form.get("confirm") != "restore"
            ):
                abort(400)
            destination = current_destination(workspace)
            if not destination:
                return expired()
            run(workspace, destination, dry_run=False)
        return redirect(f"/v/{identifier}/sandbox/archive", code=303)

    @web.post("/v/<identifier>/sandbox/archive/open")
    def archive_open(identifier):
        body = request.get_data()
        with source(identifier) as workspace:
            identity = authorize(workspace, body)
            state = workspace.get("archive", {})
            destination = current_destination(workspace)
            if (
                not destination
                or not state.get("restored")
                or identity["community_id"] != state["community_id"]
            ):
                abort(409)
            destination["restore_login"] = identity
            return redirect(
                "/v/" + destination["id"] + "/sandbox/archive/enter", code=303
            )

    @web.post("/v/<identifier>/sandbox/archive/discard")
    def archive_discard(identifier):
        body = request.get_data()
        with source(identifier) as workspace:
            identity = authorize(workspace, body)
            state = workspace.get("archive", {})
            if request.form.get("confirm") != "discard" or identity[
                "community_id"
            ] != state.get("community_id"):
                abort(400)
            destination = current_destination(workspace)
            if destination and not villages.discard(destination["id"]):
                abort(503)
            workspace.pop("archive", None)
            (workspace["directory"] / "archive-candidate.json").unlink(missing_ok=True)
        return redirect(f"/v/{identifier}/sandbox/archive", code=303)
