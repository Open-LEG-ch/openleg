# SPDX-License-Identifier: AGPL-3.0-or-later
"""Capture simulated mail inside the current disposable village."""

import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from flask import has_request_context, request


def mailbox():
    """Locate the current worker's private captured-mail directory."""
    directory = Path(os.environ["OPENLEG_SANDBOX_DIRECTORY"]).resolve()
    if os.environ.get("OPENLEG_SANDBOX_WORKER") != "1" or directory != Path.cwd():
        raise RuntimeError("simulated mail requires a village worker")
    return directory / "mail"


def capture(recipient, subject, body, *, event_key=None):
    """Capture mail locally; an optional event key suppresses repeated delivery."""
    directory = mailbox()
    directory.mkdir(mode=0o700, exist_ok=True)
    payload = {"recipient": recipient, "subject": subject, "body": body}
    if (
        has_request_context()
        and (request.endpoint or "").rsplit(".", 1)[-1] == "leg_billing_invoice_deliver"
    ):
        # The ordinary delivery command has already authorized this request.
        # Bind captured invoice mail to its immutable participant, so changing
        # persona or the profile email cannot expose another member's message.
        import database as db

        rows = db.list_community_invoices(request.view_args["community_id"])
        invoice = next(
            (row for row in rows if row["id"] == request.view_args["invoice_id"]), None
        )
        if not invoice:
            return False
        payload["owner"] = invoice["participant_id"]
    name = (
        hashlib.sha256(event_key.encode()).hexdigest()
        if event_key is not None
        else secrets.token_hex(16)
    )
    target = directory / (name + ".json")
    temporary = directory / (secrets.token_hex(16) + ".pending")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False)
        try:
            os.link(temporary, target)
        except FileExistsError:
            if event_key is None:
                raise
    finally:
        temporary.unlink(missing_ok=True)
    return True


def messages(*, viewer=None):
    """List mail visible to this viewer and extract links within the village."""
    directory = mailbox()
    origin = urlsplit(os.environ["APP_BASE_URL"])
    result = []
    for path in sorted(
        directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )[:100]:
        message = json.loads(path.read_text(encoding="utf-8"))
        if "owner" in message and message["owner"] != viewer:
            continue
        message["links"] = []
        for value in re.findall(r'https?://[^\s<>"\']+', message["body"]):
            parsed = urlsplit(value)
            if (parsed.scheme, parsed.netloc) == (
                origin.scheme,
                origin.netloc,
            ) and not parsed.path.startswith("//"):
                message["links"].append(
                    urlunsplit(("", "", parsed.path, parsed.query, ""))
                )
        result.append(message)
    return result
