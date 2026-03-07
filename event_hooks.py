"""Lightweight event hook pub/sub for LEA event-driven triggers."""

import logging

logger = logging.getLogger(__name__)

_hooks = {}


def register(event: str, callback):
    """Register a callback for an event type."""
    if event not in _hooks:
        _hooks[event] = []
    _hooks[event].append(callback)


def fire(event: str, payload: dict):
    """Fire all callbacks for an event. Exception-safe: log + continue."""
    for cb in _hooks.get(event, []):
        try:
            cb(payload)
        except Exception as e:
            logger.error(f"[event_hooks] hook error on '{event}': {e}")


def clear():
    """Clear all registered hooks. Used in tests."""
    _hooks.clear()
