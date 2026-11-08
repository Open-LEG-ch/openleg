# SPDX-License-Identifier: AGPL-3.0-or-later
"""Security denial logs use the client address established by proxy trust."""

import logging

import pytest

from tests.security_review_fixtures import make_web


@pytest.mark.parametrize("hops,expected", [(0, "192.0.2.5"), (1, "198.51.100.9")])
def test_forwarded_headers_do_not_override_the_resolved_security_log_address(
    monkeypatch, caplog, hops, expected
):
    monkeypatch.setenv("ADMIN_TOKEN", "private-admin-credential")
    web = make_web(TRUSTED_PROXY_HOPS=hops)
    with caplog.at_level(logging.WARNING, logger="security_utils"):
        response = web.test_client().get(
            "/admin/overview",
            headers={"X-Forwarded-For": "203.0.113.9, 198.51.100.9"},
            environ_overrides={"REMOTE_ADDR": "192.0.2.5"},
        )
    assert response.status_code == 403
    assert f"IP: {expected} |" in caplog.text
    assert "IP: 203.0.113.9 |" not in caplog.text
