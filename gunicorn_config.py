# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keep membership credentials out of Gunicorn access records."""

import re
from urllib.parse import unquote

from gunicorn.glogging import Logger

_MEMBERSHIP_CREDENTIAL = re.compile(
    r"(/verein/(?:bestaetigen|mitglied/zugang)/)[^/?\s\"']+"
)


class PrivateAccessLogger(Logger):
    def atoms(self, resp, req, environ, request_time):
        atoms = super().atoms(resp, req, environ, request_time)
        # Include URI, Referer and environment/header atoms, even when a future
        # access format starts using fields beyond Gunicorn's defaults.
        return {
            key: "[redacted]"
            if isinstance(value, str) and _MEMBERSHIP_CREDENTIAL.search(unquote(value))
            else value
            for key, value in atoms.items()
        }


logger_class = PrivateAccessLogger
