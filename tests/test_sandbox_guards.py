# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verify the sandbox's process boundary and normal product HTTP behavior."""

import os
import subprocess
import sys
from unittest.mock import patch

from tests.native_environment import isolated_environment


def test_worker_network_boundary_allows_unix_and_refuses_internet_sockets():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import socket
from sandbox_network import deny_internet
deny_internet()
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM):
    pass
for family in (socket.AF_INET, socket.AF_INET6, socket.AF_NETLINK):
    try:
        with socket.socket(family, socket.SOCK_STREAM):
            raise AssertionError('non-Unix socket was permitted')
    except PermissionError:
        pass
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_normal_product_does_not_offer_demo_authentication():
    with patch.dict(
        os.environ,
        isolated_environment({"DATABASE_URL": "", "REDIS_URL": "memory://"}),
        clear=True,
    ):
        from app import create_app

        web = create_app(
            {
                "SECRET_KEY": "normal-product-test",
                "APP_BASE_URL": "http://localhost",
                "RATELIMIT_STORAGE_URI": "memory://",
            },
            load_environment=False,
            check_database=False,
        )
        client = web.test_client()
        assert client.get("/sandbox/enter").status_code == 404
        assert client.post("/sandbox/start").status_code == 404
        assert "Simulation · Sonnwil" not in client.get("/login").text
