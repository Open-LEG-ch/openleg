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
        for key, value in atoms.items():
            if not isinstance(value, str):
                continue
            for _ in range(8):
                if _MEMBERSHIP_CREDENTIAL.search(value):
                    atoms[key] = "[redacted]"
                    break
                decoded = unquote(value)
                if decoded == value:
                    break
                value = decoded
            else:
                # Bound decoding work and hide unusually deep encodings rather
                # than risk retaining a credential in a raw access atom.
                atoms[key] = "[redacted]"
        return atoms


logger_class = PrivateAccessLogger
