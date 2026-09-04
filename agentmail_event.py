# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical AgentMail webhook event (issue #489).

One interface owns the provider payload variants behind the verified
``/api/internal/agentmail`` webhook: field precedence between the message
object and the event envelope, recipient normalization, the bounded safe
preview, and the caps. The verified route persists exactly this event
shape, so stored field names stay stable no matter which variant the
provider sends. Malformed values degrade to safe defaults instead of
raising.
"""

MAX_RECIPIENTS = 5
PREVIEW_MAX_CHARS = 280


def _mapping(value):
    """Return value when it is a dict, else an empty one."""
    return value if isinstance(value, dict) else {}


def _identity(value):
    """Normalize one email identity given as dict, string, or list."""
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        return {"email": value.get("email", ""), "name": value.get("name", "")}
    if isinstance(value, str):
        return {"email": value, "name": ""}
    return {"email": "", "name": ""}


def _recipients(value):
    """Normalize the recipient list, capped at MAX_RECIPIENTS entries."""
    if not value:
        return []
    if isinstance(value, (dict, str)):
        value = [value]
    if not isinstance(value, list):
        return []
    return [
        _identity(recipient)
        for recipient in value[:MAX_RECIPIENTS]
        if isinstance(recipient, (dict, str))
    ]


def _preview(message, data):
    """First truthy preview variant, coerced and capped at PREVIEW_MAX_CHARS."""
    preview = (
        message.get("text_preview")
        or message.get("extracted_text")
        or message.get("snippet")
        or message.get("text")
        or data.get("text_preview")
        or ""
    )
    return str(preview)[:PREVIEW_MAX_CHARS]


def build_event(data):
    """Build the canonical AgentMail event from a raw webhook payload.

    Message-level fields win over event-level fallbacks. Returns one dict
    with the stable stored field names, safe for any payload shape.
    """
    if not isinstance(data, dict):
        data = {}
    message = _mapping(data.get("message"))
    headers = _mapping(message.get("headers"))
    sender = _identity(message.get("from") or message.get("from_") or {})
    return {
        "event_type": (
            data.get("event_type") or data.get("type") or data.get("event") or "unknown"
        ),
        "event_id": data.get("event_id"),
        "inbox_id": message.get("inbox_id"),
        "message_id": (
            message.get("message_id") or message.get("id") or data.get("message_id")
        ),
        "thread_id": message.get("thread_id") or data.get("thread_id"),
        "from_email": sender.get("email") or headers.get("from") or "",
        "from_name": sender.get("name") or "",
        "to": _recipients(message.get("to")),
        "subject": message.get("subject") or headers.get("subject") or "",
        "received_at": (
            message.get("received_at")
            or message.get("timestamp")
            or data.get("received_at")
            or data.get("timestamp")
        ),
        "text_preview": _preview(message, data),
    }
