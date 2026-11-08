# SPDX-License-Identifier: AGPL-3.0-or-later
"""Revoke retired login cookies within one disposable village, across reloads."""

import fcntl
import os
import re
import secrets
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from flask import abort, g, request, session

import database as db
from dashboard_routes import _clear_dashboard_session, _set_dashboard_session

_IDENTITIES = ("dashboard_building_id", "municipality_id", "utility_client_id")
_EXCHANGES = {
    "main.dashboard_access_exchange": ("dashboard_building_id", "/dashboard"),
    "municipality.access_exchange": ("municipality_id", "/gemeinde/dashboard"),
    "utility.login_access": ("utility_client_id", "/utility/dashboard"),
}


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

    @staticmethod
    def _successful_exchange(response, current):
        """Recognize the ordinary auth handlers' successful redirect contract."""
        exchange = _EXCHANGES.get(request.endpoint)
        if not exchange or request.method not in {"GET", "HEAD"}:
            return None
        field, target = exchange
        location = urlsplit(response.headers.get("Location", ""))
        if (
            response.status_code == 302
            and current.get(field)
            and not location.netloc
            and location.path.removeprefix(request.script_root) == target
            and "access" not in parse_qs(location.query)
        ):
            return field
        return None

    def install(self, web):
        def before_profile_deletion_commit():
            generation = self._rotate()
            _clear_dashboard_session()
            g.sandbox_generation = generation
            g.sandbox_identity = self._identity()

        web.extensions["before_profile_deletion_commit"] = (
            before_profile_deletion_commit
        )

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
            building_id = session.get("dashboard_building_id")
            if building_id:
                profile = db.get_building(building_id)
                if not profile or not profile.get("verified"):
                    _clear_dashboard_session()

        @web.after_request
        def remember_village_login(response):
            generation = g.get("sandbox_generation")
            if generation is None:
                return response
            current = self._identity()
            previous = g.sandbox_identity
            explicit = g.get("sandbox_new_login", False)
            login_field = self._successful_exchange(response, current)
            if explicit or login_field or current != previous:
                # Every accepted magic link retires copied cookies, including
                # a new login to the same identity. Rejected links do not.
                if not explicit:
                    changed = {
                        key: value
                        for key, value in current.items()
                        if previous.get(key) != value
                    }
                    if login_field:
                        self._set_identity(login_field, current[login_field])
                    elif len(current) > 1 or "utility_client_id" in changed:
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
