# SPDX-License-Identifier: AGPL-3.0-or-later
"""The application config is a value, not a side effect of building an app.

`create_app` carried about 110 lines of environment parsing and validation that
has nothing to do with Flask. It could only be reached by constructing an
application, so every test of a hostname rule paid for one, and the rules that
guard `PUBLIC_SITE_URL` (which templates interpolate into links) were reachable
only through the factory.
"""

import pytest

import app_config


def _env(**overrides):
    base = {"APP_BASE_URL": "http://localhost:5003"}
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# The dashboard token TTLs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    (
        pytest.param(None, 900, id="unset-falls-back"),
        pytest.param("1800", 1800, id="parsed"),
        pytest.param("not-a-number", 900, id="unparseable-falls-back"),
        pytest.param("10", 60, id="clamped-up-to-a-minute"),
        pytest.param("999999", 86_400, id="clamped-down-to-a-day"),
    ),
)
def test_the_dashboard_ttl_is_parsed_and_bounded(raw, expected):
    env = _env()
    if raw is not None:
        env["DASHBOARD_ACCESS_TOKEN_TTL_SECONDS"] = raw

    config = app_config.build_config(env)

    assert config["DASHBOARD_ACCESS_TOKEN_TTL_SECONDS"] == expected


# ---------------------------------------------------------------------------
# The public site origin
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    (
        pytest.param("https://openleg.ch", "https://openleg.ch", id="bare"),
        pytest.param("https://openleg.ch/", "https://openleg.ch", id="trailing-slash"),
        pytest.param("https://openleg.ch:8443", "https://openleg.ch:8443", id="port"),
        pytest.param("http://127.0.0.1:5000", "http://127.0.0.1:5000", id="ip"),
    ),
)
def test_a_valid_public_site_url_is_reduced_to_its_origin(value, expected):
    assert app_config.validated_public_site_url(value) == expected


@pytest.mark.parametrize(
    "value, message",
    (
        pytest.param("openleg.ch", r"absolute HTTP\(S\) URL", id="no-scheme"),
        pytest.param(
            " https://openleg.ch", r"absolute HTTP\(S\) URL", id="leading-space"
        ),
        pytest.param("https://openleg.ch\n", r"absolute HTTP\(S\) URL", id="newline"),
        pytest.param("https://[::1", r"absolute HTTP\(S\) URL", id="unclosed-bracket"),
        pytest.param(
            "https://user:secret@openleg.ch",
            r"credentials or suffixes",
            id="credentials",
        ),
        pytest.param("https://openleg.ch/path", r"credentials or suffixes", id="path"),
        pytest.param(
            "https://openleg.ch?next=x", r"credentials or suffixes", id="query"
        ),
        pytest.param("https://openleg.ch:", r"valid port", id="empty-port"),
        pytest.param("https://-openleg.ch", r"valid hostname", id="leading-hyphen"),
    ),
)
def test_a_malformed_public_site_url_is_refused(value, message):
    with pytest.raises(ValueError, match=message):
        app_config.validated_public_site_url(value)


# ---------------------------------------------------------------------------
# Cookie security, which has three sources and a precedence between them
# ---------------------------------------------------------------------------


def test_a_plain_http_base_url_leaves_the_session_cookie_insecure():
    config = app_config.build_config(_env(APP_BASE_URL="http://localhost:5003"))

    assert config["SESSION_COOKIE_SECURE"] is False


def test_an_https_base_url_secures_the_session_cookie():
    config = app_config.build_config(_env(APP_BASE_URL="https://openleg.ch"))

    assert config["SESSION_COOKIE_SECURE"] is True


@pytest.mark.parametrize("raw, expected", (("true", True), ("0", False), ("ON", True)))
def test_the_environment_overrides_the_inference(raw, expected):
    config = app_config.build_config(
        _env(APP_BASE_URL="http://localhost:5003", SESSION_COOKIE_SECURE=raw)
    )

    assert config["SESSION_COOKIE_SECURE"] is expected


def test_an_explicit_false_survives_an_https_base_url():
    """An override says so on purpose; the https inference must not undo it."""
    config = app_config.build_config(
        _env(APP_BASE_URL="https://openleg.ch"), {"SESSION_COOKIE_SECURE": False}
    )

    assert config["SESSION_COOKIE_SECURE"] is False


def test_an_explicit_override_beats_the_environment():
    config = app_config.build_config(
        _env(APP_BASE_URL="https://openleg.ch", SESSION_COOKIE_SECURE="false"),
        {"SESSION_COOKIE_SECURE": True},
    )

    assert config["SESSION_COOKIE_SECURE"] is True


# ---------------------------------------------------------------------------
# SITE_URL follows APP_BASE_URL unless it is given
# ---------------------------------------------------------------------------


def test_site_url_follows_the_base_url_without_its_trailing_slash():
    config = app_config.build_config(_env(APP_BASE_URL="https://openleg.ch/"))

    assert config["SITE_URL"] == "https://openleg.ch"


def test_an_overridden_base_url_carries_site_url_with_it():
    config = app_config.build_config(_env(), {"APP_BASE_URL": "https://example.ch/"})

    assert config["APP_BASE_URL"] == "https://example.ch/"
    assert config["SITE_URL"] == "https://example.ch"


def test_an_explicit_site_url_is_left_alone():
    config = app_config.build_config(
        _env(), {"APP_BASE_URL": "https://example.ch", "SITE_URL": "https://other.ch/x"}
    )

    assert config["SITE_URL"] == "https://other.ch/x"


# ---------------------------------------------------------------------------
# The rest of the mapping
# ---------------------------------------------------------------------------


def test_the_defaults_do_not_need_an_environment():
    config = app_config.build_config({})

    assert config["APP_BASE_URL"] == "http://localhost:5003"
    assert config["ALLOWED_HOSTS"] == ["localhost", "127.0.0.1"]
    assert config["PUBLIC_SITE_URL"] == "https://openleg.ch"
    assert config["SESSION_COOKIE_HTTPONLY"] is True
    assert config["MAX_CONTENT_LENGTH"] == 10 * 1024 * 1024
    assert config["CRON_SECRET"] == ""


def test_a_secret_key_is_generated_when_the_environment_omits_one():
    first = app_config.build_config({})["SECRET_KEY"]
    second = app_config.build_config({})["SECRET_KEY"]

    assert first and second and first != second


def test_no_flask_import_is_needed_to_build_a_config():
    """The point of the seam: config is a value, testable without an app."""
    import ast
    import pathlib

    tree = ast.parse(
        pathlib.Path(app_config.__file__).read_text(encoding="utf-8"),
        filename="app_config.py",
    )
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert "flask" not in imported
