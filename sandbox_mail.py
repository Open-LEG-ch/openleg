# SPDX-License-Identifier: AGPL-3.0-or-later
"""Capture simulated mail inside the current disposable village."""

import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def mailbox():
    directory = Path(os.environ["OPENLEG_SANDBOX_DIRECTORY"]).resolve()
    if os.environ.get("OPENLEG_SANDBOX_WORKER") != "1" or directory != Path.cwd():
        raise RuntimeError("simulated mail requires a village worker")
    return directory / "mail"


def capture(recipient, subject, body, *, event_key=None):
    """Capture mail locally; an optional event key suppresses repeated delivery."""
    directory = mailbox()
    directory.mkdir(mode=0o700, exist_ok=True)
    payload = {"recipient": recipient, "subject": subject, "body": body}
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


def messages():
    directory = mailbox()
    origin = urlsplit(os.environ["APP_BASE_URL"])
    result = []
    for path in sorted(
        directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )[:100]:
        message = json.loads(path.read_text(encoding="utf-8"))
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
