# SPDX-License-Identifier: AGPL-3.0-or-later
"""Distinct forwarded clients behind one proxy get distinct limiter buckets.

With single-hop proxy trust explicitly enabled (OPENLEG_TRUSTED_PROXY_HOPS=1),
the app must rate-limit on the forwarded client address, not on the proxy
address. Otherwise two neighbours behind the same proxy share one bucket and
the second one is throttled by the first one's traffic.
"""

import copy

import pytest
from flask import jsonify
from flask import request as flask_request

import app as app_module
import security_extensions
import tenant as tenant_module


@pytest.fixture()
def proxy_probe_app(monkeypatch, request):
    """Real app factory with single-hop proxy trust enabled and only the
    tenant seam stubbed, so the probe needs no database or cache. The limiter
    and every factory middleware stay real. Reusable for later controls."""
    mode = getattr(request, "param", "1")
    if mode is None:
        monkeypatch.delenv("OPENLEG_TRUSTED_PROXY_HOPS", raising=False)
    else:
        monkeypatch.setenv("OPENLEG_TRUSTED_PROXY_HOPS", mode)
    monkeypatch.setenv("OPENLEG_RELEASE_READ_ONLY", "0")
    monkeypatch.setattr(
        tenant_module,
        "get_tenant_config",
        lambda *args, **kwargs: copy.deepcopy(tenant_module.DEFAULT_TENANT),
    )

    application = app_module.create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "proxy-client-identity-test",
            "APP_BASE_URL": "http://localhost:5003",
            "PUBLIC_SITE_URL": "https://openleg.ch",
            "ALLOWED_HOSTS": ["localhost"],
            "BABEL_DEFAULT_LOCALE": "de_CH",
            "SESSION_COOKIE_SECURE": False,
            "RATELIMIT_STORAGE_URI": "memory://",
            "RATELIMIT_ENABLED": True,
        },
        load_environment=False,
        check_database=False,
    )

    @application.route("/proxy-limit-probe")
    @security_extensions.limiter.limit("2 per minute")
    def proxy_limit_probe():
        return jsonify(
            {
                "remote_addr": flask_request.remote_addr,
                "host": flask_request.host,
                "scheme": flask_request.scheme,
                "server_port": flask_request.environ["SERVER_PORT"],
                "script_root": flask_request.script_root,
            }
        )

    security_extensions.limiter.reset()
    yield application
    security_extensions.limiter.reset()


def test_single_hop_proxy_clients_get_distinct_limiter_buckets(proxy_probe_app):
    client = proxy_probe_app.test_client()
    proxy_env = {"REMOTE_ADDR": "192.0.2.200"}

    responses = [
        client.get(
            "/proxy-limit-probe",
            headers={"X-Forwarded-For": forwarded_client},
            environ_overrides=proxy_env,
        )
        for forwarded_client in ("198.51.100.10", "198.51.100.10", "198.51.100.11")
    ]

    statuses = [response.status_code for response in responses]
    assert statuses == [200, 200, 200], statuses

    assert responses[0].get_json()["remote_addr"] == "198.51.100.10"
    assert responses[1].get_json()["remote_addr"] == "198.51.100.10"
    assert responses[2].get_json()["remote_addr"] == "198.51.100.11"


def test_one_hop_selection_takes_rightmost_forwarded_client(proxy_probe_app):
    client = proxy_probe_app.test_client()
    proxy_env = {"REMOTE_ADDR": "192.0.2.200"}

    responses = [
        client.get(
            "/proxy-limit-probe",
            headers={"X-Forwarded-For": forwarded_chain},
            environ_overrides=proxy_env,
        )
        for forwarded_chain in (
            "203.0.113.1, 198.51.100.10",
            "203.0.113.2, 198.51.100.10",
            "203.0.113.3, 198.51.100.10",
            "203.0.113.3, 198.51.100.11",
        )
    ]

    statuses = [response.status_code for response in responses]
    assert statuses == [200, 200, 429, 200], statuses

    assert responses[0].get_json()["remote_addr"] == "198.51.100.10"
    assert responses[1].get_json()["remote_addr"] == "198.51.100.10"
    assert responses[3].get_json()["remote_addr"] == "198.51.100.11"


def test_one_hop_without_forwarded_header_falls_back_to_peer(proxy_probe_app):
    client = proxy_probe_app.test_client()

    responses = [
        client.get(
            "/proxy-limit-probe",
            environ_overrides={"REMOTE_ADDR": remote_addr},
        )
        for remote_addr in ("192.0.2.10", "192.0.2.10", "192.0.2.10", "192.0.2.11")
    ]

    statuses = [response.status_code for response in responses]
    assert statuses == [200, 200, 429, 200], statuses

    assert responses[0].get_json()["remote_addr"] == "192.0.2.10"
    assert responses[3].get_json()["remote_addr"] == "192.0.2.11"


@pytest.mark.parametrize("proxy_probe_app", [None, "0"], indirect=True)
def test_default_direct_ignores_forged_forwarding(proxy_probe_app):
    client = proxy_probe_app.test_client()

    responses = [
        client.get(
            "/proxy-limit-probe",
            headers={"X-Forwarded-For": forwarded_client},
            environ_overrides={"REMOTE_ADDR": remote_addr},
        )
        for remote_addr, forwarded_client in (
            ("192.0.2.10", "198.51.100.1"),
            ("192.0.2.10", "198.51.100.2"),
            ("192.0.2.10", "198.51.100.3"),
            ("192.0.2.11", "198.51.100.3"),
        )
    ]

    statuses = [response.status_code for response in responses]
    assert statuses == [200, 200, 429, 200], statuses

    assert responses[0].get_json()["remote_addr"] == "192.0.2.10"
    assert responses[1].get_json()["remote_addr"] == "192.0.2.10"
    assert responses[3].get_json()["remote_addr"] == "192.0.2.11"


def test_opt_in_trust_changes_only_client_address(proxy_probe_app):
    client = proxy_probe_app.test_client()

    response = client.get(
        "/proxy-limit-probe",
        headers={
            "X-Forwarded-For": "198.51.100.10",
            "X-Forwarded-Host": "evil.example:9999",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Port": "9999",
            "X-Forwarded-Prefix": "/evil",
        },
        environ_overrides={
            "REMOTE_ADDR": "192.0.2.200",
            "HTTP_HOST": "localhost:5003",
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "5003",
            "wsgi.url_scheme": "http",
            "SCRIPT_NAME": "",
        },
    )

    assert response.status_code == 200

    payload = response.get_json()
    assert payload["remote_addr"] == "198.51.100.10"
    assert payload["host"] == "localhost:5003"
    assert payload["scheme"] == "http"
    assert payload["server_port"] == "5003"
    assert payload["script_root"] == ""


@pytest.mark.parametrize(
    "malformed_hops",
    ["", "2", "-1", "true", " 1", "1 ", "01", "1.0"],
)
def test_malformed_trusted_proxy_hops_env_fails_factory(monkeypatch, malformed_hops):
    monkeypatch.setenv("OPENLEG_TRUSTED_PROXY_HOPS", malformed_hops)
    monkeypatch.setenv("OPENLEG_RELEASE_READ_ONLY", "0")

    with pytest.raises(ValueError, match="OPENLEG_TRUSTED_PROXY_HOPS"):
        app_module.create_app(
            {
                "TESTING": True,
                "APP_BASE_URL": "http://localhost:5003",
                "PUBLIC_SITE_URL": "https://openleg.ch",
                "RATELIMIT_STORAGE_URI": "memory://",
            },
            load_environment=False,
            check_database=False,
        )


def test_explicit_config_override_wins_over_trusted_proxy_hops_env():
    config = app_module.app_config.build_config(
        {"OPENLEG_TRUSTED_PROXY_HOPS": "1"},
        {"TRUSTED_PROXY_HOPS": 0},
    )

    assert config["TRUSTED_PROXY_HOPS"] == 0
    assert type(config["TRUSTED_PROXY_HOPS"]) is int


@pytest.mark.parametrize("invalid_override", [True, False, 1.0, None, 2, "2"])
def test_invalid_explicit_trusted_proxy_hops_override_fails_config(invalid_override):
    with pytest.raises(ValueError, match="OPENLEG_TRUSTED_PROXY_HOPS"):
        app_module.app_config.build_config(
            {"OPENLEG_TRUSTED_PROXY_HOPS": "1"},
            {"TRUSTED_PROXY_HOPS": invalid_override},
        )
