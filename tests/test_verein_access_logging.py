# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exercise Gunicorn's actual access formatting with credential-bearing URLs."""

from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import quote, unquote

import pytest
from gunicorn.config import Config
from gunicorn.glogging import SafeAtoms

from gunicorn_config import PrivateAccessLogger


@pytest.mark.parametrize("prefix", ["/verein/bestaetigen/", "/verein/mitglied/zugang/"])
@pytest.mark.parametrize("encoding_depth", [0, 1, 2, 4, 12])
def test_access_log_redacts_membership_credentials(prefix, encoding_depth):
    token = "fictional-secret-credential"
    path = prefix + token
    for _ in range(encoding_depth):
        path = "/" + quote(path.removeprefix("/"), safe="")
    logger = PrivateAccessLogger(Config())
    environ = {
        "REQUEST_METHOD": "GET",
        "RAW_URI": path + "?lang=fr",
        "PATH_INFO": unquote(path),
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


def test_access_log_keeps_ordinary_request_details():
    path = "/verein/spenden?amount=50"
    logger = PrivateAccessLogger(Config())
    atoms = logger.atoms(
        SimpleNamespace(status="200 OK", sent=123, headers={}),
        {},
        {
            "REQUEST_METHOD": "GET",
            "RAW_URI": path,
            "PATH_INFO": "/verein/spenden",
            "QUERY_STRING": "amount=50",
            "SERVER_PROTOCOL": "HTTP/1.1",
        },
        timedelta(seconds=1),
    )
    record = Config().access_log_format % SafeAtoms(atoms)
    assert "GET " + path + " HTTP/1.1" in record
    assert "200 123" in record
