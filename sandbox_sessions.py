# SPDX-License-Identifier: AGPL-3.0-or-later
"""Revoke retired login cookies within one disposable village, across reloads."""

import fcntl
import os
import re
import secrets
from contextlib import contextmanager
from pathlib import Path

from flask import abort, g, session

from dashboard_routes import _clear_dashboard_session, _set_dashboard_session

_IDENTITIES = ("dashboard_building_id", "municipality_id", "utility_client_id")


class VillageSessions:
    """Bind signed product cookies to the village's current login generation."""

    def __init__(self, directory):
        self.path = Path(directory) / ".session-generation"
        with self._store(create=True) as store:
            if not store.read():
                self._replace(store)

    @contextmanager
    def _store(self, *, create=False):
        flags = os.O_RDWR | (os.O_CREAT if create else 0)
        with os.fdopen(os.open(self.path, flags, 0o600), "r+") as store:
            fcntl.flock(store, fcntl.LOCK_EX)
            yield store

    @staticmethod
    def _replace(store):
        generation = secrets.token_hex(32)
        store.seek(0)
        store.write(generation)
        store.truncate()
        store.flush()
        os.fsync(store.fileno())
        return generation

    def _current(self):
        with self._store() as store:
            generation = store.read()
        if not re.fullmatch(r"[a-f0-9]{64}", generation):
            raise RuntimeError("sandbox login generation unavailable")
        return generation

    def _rotate(self):
        with self._store() as store:
            return self._replace(store)

    @staticmethod
    def _identity():
        return {key: session[key] for key in _IDENTITIES if session.get(key)}

    @staticmethod
    def _set_identity(field, identifier):
        if field not in _IDENTITIES:
            raise ValueError("unknown sandbox identity")
        if field == "dashboard_building_id":
            _set_dashboard_session(identifier)
        else:
            _clear_dashboard_session()
            session.permanent = True
            session[field] = identifier
            if field == "municipality_id":
                session["municipality_csrf_token"] = secrets.token_urlsafe(32)
        session["sandbox_csrf"] = secrets.token_urlsafe(32)

    def sign_in(self, field, identifier):
        """Replace role context and revoke earlier cookies when the response commits."""
        self._set_identity(field, identifier)
        g.sandbox_new_login = True

    def install(self, web):
        @web.before_request
        def bind_village_login():
            g.sandbox_generation = None
            try:
                generation = self._current()
            except (OSError, RuntimeError):
                _clear_dashboard_session()
                abort(503)
            if session.get("sandbox_generation") != generation:
                _clear_dashboard_session()
            g.sandbox_generation = generation
            g.sandbox_identity = self._identity()

        @web.after_request
        def remember_village_login(response):
            generation = g.get("sandbox_generation")
            if generation is None:
                return response
            current = self._identity()
            previous = g.sandbox_identity
            explicit = g.get("sandbox_new_login", False)
            if explicit or current != previous:
                # Ordinary magic links and logout also change identity. Utility
                # login retains old session fields, so clear those at this seam.
                if not explicit:
                    changed = {
                        key: value
                        for key, value in current.items()
                        if previous.get(key) != value
                    }
                    if len(current) > 1 or "utility_client_id" in changed:
                        if len(changed) != 1:
                            _clear_dashboard_session()
                            return web.make_response(("", 503))
                        self._set_identity(*next(iter(changed.items())))
                    elif not current:
                        _clear_dashboard_session()
                try:
                    generation = self._rotate()
                except (OSError, RuntimeError):
                    _clear_dashboard_session()
                    return web.make_response(("", 503))
            session["sandbox_generation"] = generation
            return response
