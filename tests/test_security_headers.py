# SPDX-License-Identifier: AGPL-3.0-or-later
"""Header and cookie posture, audited and pinned (#526).

The audit table is in the PR. Pins fail when a header is dropped or a cookie
flag loosens. Intentional exceptions carry their reason here:

- ``script-src``/``style-src`` allow ``'unsafe-inline'`` and three document
  CDNs (unpkg, jsdelivr, Google Fonts) because product templates inline their
  scripts and load Leaflet, Swagger UI, Chart.js and fonts from those CDNs.
- ``script-src``/``connect-src`` allow ``https://stats.openleg.ch`` for the
  cookieless Matomo tracker (#562); it only renders when ``MATOMO_SITE_ID``
  is configured, so unset deployments make no tracking requests.
- ``img-src`` allows ``https:`` for remote images (municipality and user
  content); ``http:`` was removed, nothing referenced it and mixed content
  would block it anyway.
- ``force_https`` follows the configured ``APP_BASE_URL`` scheme, so local
  HTTP deployments stay servable; ``SESSION_COOKIE_SECURE`` derives the same
  way with an explicit environment override.
- ``Referrer-Policy: strict-origin-when-cross-origin`` is Talisman's default;
  private invoice surfaces additionally pin ``no-referrer`` (their own tests).
"""

from unittest import mock

import pytest


def _make_app(**overrides):
    with (
        mock.patch("database.is_db_available", return_value=True),
        mock.patch("database.init_db", return_value=True),
        mock.patch("database.get_stats", return_value={"total_buildings": 0}),
        mock.patch("database.seed_default_tenant", return_value=True),
    ):
        import app as app_module

        config = {
            "TESTING": True,
            "SECRET_KEY": "header-posture-key",
            "APP_BASE_URL": "http://localhost:5003",
            "RATELIMIT_STORAGE_URI": "memory://",
            **overrides,
        }
        return app_module.create_app(
            config, load_environment=False, check_database=False
        )


@pytest.fixture(scope="module")
def client():
    application = _make_app()
    with application.test_client() as test_client:
        yield test_client


_SECURITY_HEADERS = (
    "X-Frame-Options",
    "X-Content-Type-Options",
    "Content-Security-Policy",
    "Referrer-Policy",
)


class TestHeadersOnEverySurface:
    """One representative response per surface class carries the full set."""

    @pytest.mark.parametrize(
        "path",
        [
            "/",  # public marketing
            "/leg-kalkulator",  # public calculator page
            "/api/v1/municipalities",  # public API
            "/health",  # infrastructure
        ],
    )
    def test_security_headers_are_present(self, client, path):
        response = client.get(path)
        for header in _SECURITY_HEADERS:
            assert header in response.headers, f"{path} misses {header}"

    def test_csp_keeps_script_sources_narrow(self, client):
        response = client.get("/")
        csp = response.headers["Content-Security-Policy"]
        script_part = next(
            part for part in csp.split(";") if part.strip().startswith("script-src")
        )
        assert "'self'" in script_part
        assert "http:" not in script_part, (
            "script-src must not allow arbitrary http sources"
        )
        for allowed in ("unpkg.com", "cdn.jsdelivr.net", "www.googletagmanager.com"):
            assert allowed in script_part, "documented CDN exception went missing"

    def test_img_src_no_longer_allows_plain_http(self, client):
        response = client.get("/")
        csp = response.headers["Content-Security-Policy"]
        img_part = next(
            part for part in csp.split(";") if part.strip().startswith("img-src")
        )
        assert "http:" not in img_part.replace("https:", ""), (
            "img-src must not allow arbitrary http images"
        )


class TestCookielessMatomoTracking:
    """The product app tracks cookieless, like the public site (#562)."""

    def test_csp_allows_the_tracker_subdomain_for_scripts_and_connects(self, client):
        csp = client.get("/").headers["Content-Security-Policy"]
        script_tokens = next(
            part.strip().split()
            for part in csp.split(";")
            if part.strip().startswith("script-src")
        )
        connect_tokens = next(
            part.strip().split()
            for part in csp.split(";")
            if part.strip().startswith("connect-src")
        )
        assert "https://stats.openleg.ch" in script_tokens
        assert "https://stats.openleg.ch" in connect_tokens
        assert not any(
            token.startswith("https://stats.openleg.ch.")
            for token in script_tokens + connect_tokens
        )

    def test_the_tracker_is_absent_while_the_site_id_is_unset(self, client):
        rendered = client.get("/").get_data(as_text=True)

        assert "matomo.js" not in rendered
        assert "stats.openleg.ch" not in rendered

    def test_a_configured_site_id_renders_the_cookieless_tracker(self):
        application = _make_app(MATOMO_SITE_ID="1")
        rendered = application.test_client().get("/").get_data(as_text=True)

        assert "https://stats.openleg.ch/matomo.js" in rendered
        assert "['disableCookies']" in rendered
        assert "['setDoNotTrack', true]" in rendered
        assert "['setSiteId', '1']" in rendered
        assert "https://stats.openleg.ch/matomo.php" in rendered

    def test_the_dashboard_shell_carries_the_tracker_too(self):
        application = _make_app(MATOMO_SITE_ID="1")

        assert "matomo.js" in application.jinja_env.get_template(
            "product_base.html"
        ).render(matomo_site_id="1")

    def test_a_malformed_site_id_refuses_to_build_the_app(self):
        with pytest.raises(ValueError, match="positive integer"):
            _make_app(MATOMO_SITE_ID="1'];alert(1)//")


class TestSessionCookieFlags:
    def test_config_defaults_are_the_intended_posture(self):
        import app_config

        config = app_config.build_config({"APP_BASE_URL": "https://openleg.ch"})
        assert config["SESSION_COOKIE_HTTPONLY"] is True
        assert config["SESSION_COOKIE_SAMESITE"] == "Lax"
        assert config["SESSION_COOKIE_SECURE"] is True

    def test_secure_cookie_env_override_wins(self):
        import app_config

        config = app_config.build_config(
            {"APP_BASE_URL": "https://openleg.ch", "SESSION_COOKIE_SECURE": "false"}
        )
        assert config["SESSION_COOKIE_SECURE"] is False

    def test_http_base_url_does_not_force_secure_flag(self):
        import app_config

        config = app_config.build_config({"APP_BASE_URL": "http://localhost:5003"})
        assert config["SESSION_COOKIE_SECURE"] is False

    def test_session_cookie_is_httponly_and_samesite_lax(self):
        application = _make_app()
        client = application.test_client()

        with client.session_transaction() as state:
            state["dashboard_building_id"] = "b1"

        page = client.get("/livez")
        assert page.status_code == 200
        assert application.config["SESSION_COOKIE_HTTPONLY"] is True
        assert application.config["SESSION_COOKIE_SAMESITE"] == "Lax"
        set_cookie = getattr(client, "_cookies", {})
        assert set_cookie, "a session cookie must have been set by the request"
        for cookie in set_cookie.values():
            if cookie.key == "session":
                assert cookie.http_only is True, "the session cookie must be HttpOnly"
                assert cookie.same_site == "Lax", (
                    "the session cookie must be SameSite=Lax"
                )

    def test_https_base_url_marks_the_session_cookie_secure(self):
        application = _make_app(APP_BASE_URL="https://openleg.ch")
        client = application.test_client()

        with client.session_transaction() as state:
            state["dashboard_building_id"] = "b1"

        client.get("/livez")
        jar = getattr(client, "_cookies", {})
        assert jar, "a session cookie must have been set by the request"
        for cookie in jar.values():
            if cookie.key == "session":
                assert cookie.secure, "the session cookie must be Secure on https"
