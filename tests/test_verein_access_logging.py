# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exercise Gunicorn's actual access formatting with credential-bearing URLs."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from gunicorn.config import Config
from gunicorn.glogging import SafeAtoms

from gunicorn_config import PrivateAccessLogger


@pytest.mark.parametrize("prefix", ["/verein/bestaetigen/", "/verein/mitglied/zugang/"])
def test_access_log_redacts_membership_credentials(prefix):
    token = "fictional-secret-credential"
    path = prefix + token
    logger = PrivateAccessLogger(Config())
    environ = {
        "REQUEST_METHOD": "GET",
        "RAW_URI": path.replace("/", "%2F") + "?lang=fr",
        "PATH_INFO": path,
        "QUERY_STRING": "lang=fr",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "HTTP_REFERER": "https://openleg.ch" + path,
    }
    atoms = logger.atoms(
        SimpleNamespace(status="200 OK", sent=123, headers={}),
        {"Referer": environ["HTTP_REFERER"]},
        environ,
        timedelta(seconds=1),
    )
    record = Config().access_log_format % SafeAtoms(atoms)
    assert token not in record
    assert "[redacted]" in record
    assert all(token not in str(value) for value in atoms.values())
