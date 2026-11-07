# SPDX-License-Identifier: AGPL-3.0-or-later
"""The application config is a value, not a side effect of building an app.

`create_app` carried about 110 lines of environment parsing and validation that
has nothing to do with Flask. It could only be reached by constructing an
application, so every test of a hostname rule paid for one, and the rules that
guard `PUBLIC_SITE_URL` (which templates interpolate into links) were reachable
only through the factory.
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import app_config
from tests.native_environment import isolated_environment as native_environment


def _env(**overrides):
    base = {"APP_BASE_URL": "http://localhost:5003"}
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "de_CH"),
        ("", "de_CH"),
        ("  ", "de_CH"),
        ("fr_CH ", "fr_CH"),
        ("de_CH", "de_CH"),
    ],
)
def test_display_locale_environment_trims_whitespace_and_defaults_empty(raw, expected):
    environment = _env()
    if raw is not None:
        environment["BABEL_DEFAULT_LOCALE"] = raw
    assert app_config.build_config(environment)["BABEL_DEFAULT_LOCALE"] == expected


@pytest.mark.parametrize(
    "override,expected",
    [
        ("", "de_CH"),
        (" ", "de_CH"),
        (" de_CH ", "de_CH"),
        ("fr_CH ", "fr_CH"),
        ("fr-CH", "fr-CH"),
    ],
)
def test_display_locale_explicit_override_wins_and_is_normalized(override, expected):
    config = app_config.build_config(
        _env(BABEL_DEFAULT_LOCALE="fr_CH"), {"BABEL_DEFAULT_LOCALE": override}
    )
    assert config["BABEL_DEFAULT_LOCALE"] == expected


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
# The cookieless Matomo site id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    (
        pytest.param("", "", id="empty-disables-tracking"),
        pytest.param(None, "", id="unset-disables-tracking"),
        pytest.param("  ", "", id="blank-is-normalized-away"),
        pytest.param("1", "1", id="single-digit"),
        pytest.param(" 7 ", "7", id="padded-is-stripped"),
        pytest.param("42", "42", id="multi-digit"),
    ),
)
def test_a_valid_matomo_site_id_is_normalized(value, expected):
    assert app_config.validated_matomo_site_id(value) == expected


@pytest.mark.parametrize(
    "value",
    (
        pytest.param("1'];alert(1)//", id="script-content"),
        pytest.param("0", id="leading-zero"),
        pytest.param("007", id="zero-padded"),
        pytest.param("-1", id="negative"),
        pytest.param("1.5", id="float"),
        pytest.param("abc", id="letters"),
        pytest.param("1 2", id="internal-space"),
        pytest.param(0, id="integer-zero-is-configured-not-unset"),
        pytest.param(False, id="false-is-configured-not-unset"),
    ),
)
def test_a_malformed_matomo_site_id_is_refused(value):
    with pytest.raises(ValueError, match="positive integer"):
        app_config.validated_matomo_site_id(value)


def test_the_environment_reaches_the_matomo_site_id_setting():
    config = app_config.build_config(_env(MATOMO_SITE_ID=" 3 "))

    assert config["MATOMO_SITE_ID"] == "3"


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


@pytest.mark.parametrize(
    "placeholder",
    [
        "your-secret-key-here",
        "generate-a-strong-random-key-here",
        "  your-secret-key-here  ",
    ],
)
def test_a_documented_placeholder_secret_key_is_refused(placeholder):
    """The example values are public, so a key copied from them signs nothing."""
    with pytest.raises(ValueError, match="SECRET_KEY"):
        app_config.build_config({"SECRET_KEY": placeholder})


def test_a_placeholder_secret_key_override_is_refused():
    with pytest.raises(ValueError, match="SECRET_KEY"):
        app_config.build_config({}, {"SECRET_KEY": "your-secret-key-here"})


@pytest.mark.parametrize(
    "key",
    [
        b"your-secret-key-here",
        b"generate-a-strong-random-key-here",
        b"  your-secret-key-here  ",
        bytearray(b"your-secret-key-here"),
        bytearray(b"generate-a-strong-random-key-here"),
        bytearray(b"  your-secret-key-here  "),
    ],
)
def test_a_placeholder_secret_key_in_bytes_is_refused(key):
    with pytest.raises(ValueError, match="SECRET_KEY"):
        app_config.build_config({}, {"SECRET_KEY": key})


@pytest.mark.parametrize(
    "key",
    [
        b"\x00\xff\xfe-synthetic-random-key",
        bytearray(b"\x00\xff\xfe-synthetic-random-key"),
    ],
)
def test_a_real_binary_secret_key_is_preserved(key):
    config = app_config.build_config({}, {"SECRET_KEY": key})

    assert config["SECRET_KEY"] == key
    assert config["SECRET_KEY"] is key


def test_a_configured_secret_key_is_kept():
    config = app_config.build_config({"SECRET_KEY": "a-real-random-key"})

    assert config["SECRET_KEY"] == "a-real-random-key"


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


@pytest.fixture
def secret_key_factory():
    with patch.dict(os.environ, native_environment({}), clear=True):
        import app as app_module

        yield app_module.create_app


def test_the_app_refuses_to_start_with_a_placeholder_secret_key(secret_key_factory):
    """The guard runs on the real factory path, not only in build_config."""
    with pytest.raises(ValueError, match="SECRET_KEY"):
        secret_key_factory(
            {
                "TESTING": True,
                "SECRET_KEY": "your-secret-key-here",
                "RATELIMIT_STORAGE_URI": "memory://",
            },
            load_environment=False,
            check_database=False,
        )


@pytest.mark.parametrize(
    "key", [b"your-secret-key-here", bytearray(b"your-secret-key-here")]
)
def test_the_app_refuses_to_start_with_a_placeholder_secret_key_in_bytes(
    key, secret_key_factory
):
    with pytest.raises(ValueError, match="SECRET_KEY"):
        secret_key_factory(
            {
                "TESTING": True,
                "SECRET_KEY": key,
                "RATELIMIT_STORAGE_URI": "memory://",
            },
            load_environment=False,
            check_database=False,
        )


def test_the_app_starts_with_a_real_secret_key(secret_key_factory):
    application = secret_key_factory(
        {
            "TESTING": True,
            "SECRET_KEY": "a-real-random-key",
            "RATELIMIT_STORAGE_URI": "memory://",
        },
        load_environment=False,
        check_database=False,
    )

    assert application.config["SECRET_KEY"] == "a-real-random-key"


def test_factory_secret_key_cases_ignore_inherited_application_settings(tmp_path):
    environment = {
        **native_environment({}),
        "PERMANENT_SESSION_LIFETIME": "foreign-setting",
        "BABEL_DEFAULT_LOCALE": "unsupported-host-locale",
        "PUBLIC_SITE_URL": "not-an-origin",
        "OPENLEG_TRUSTED_PROXY_HOPS": "2",
        "UNRECOGNIZED_HOST_SETTING": "must-not-leak",
        "PYTEST_ADDOPTS": "",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_app_config.py::test_the_app_refuses_to_start_with_a_placeholder_secret_key",
            "tests/test_app_config.py::test_the_app_refuses_to_start_with_a_placeholder_secret_key_in_bytes",
            "tests/test_app_config.py::test_the_app_starts_with_a_real_secret_key",
            "-q",
            "--tb=short",
            "-o",
            "addopts=",
            "-o",
            f"cache_dir={tmp_path / 'cache'}",
            f"--basetemp={tmp_path / 'temp'}",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "skipped" not in output.lower(), output
