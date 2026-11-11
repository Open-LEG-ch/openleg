# SPDX-License-Identifier: AGPL-3.0-or-later
"""Request-local attribution, separate from the building authorization principal."""

from contextlib import contextmanager
from contextvars import ContextVar

_actor = ContextVar("operator_actor", default=None)


def snapshot():
    actor = _actor.get()
    return dict(actor) if actor else None


@contextmanager
def acting_as(client):
    token = _actor.set(
        {
            "initiating_client_id": client["id"],
            "actor_kind": client.get("actor_kind", "service"),
            "agent_label": client.get("agent_label"),
        }
    )
    try:
        yield
    finally:
        _actor.reset(token)
